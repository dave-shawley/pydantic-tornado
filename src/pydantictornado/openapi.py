import collections.abc
import datetime
import functools
import inspect
import ipaddress
import re
import types
import typing
import uuid
import warnings

import pydantic
import tornado.routing
import tornado.web
import yarl

from pydantictornado import errors, metadata, request_handling, routing, util


class OperationAnnotation(pydantic.BaseModel):
    """Meta information for OpenAPI Operations

    Use the [describe_operation][] function to annotate
    operations when you create the route.
    """

    summary: str | None = None
    description: str | None = None
    operation_id: str | None = None
    deprecated: bool | None = None
    tags: list[str] = pydantic.Field(default_factory=list)


class Omit:
    """Use this annotation to omit a method from the OpenAPI specification"""


PS = typing.ParamSpec('PS')


def omit(o: typing.Callable[PS, util.T]) -> typing.Callable[PS, util.T]:
    """Prevent something from being described in the OpenAPI specification"""
    return metadata.append(o, Omit())


class SchemaExtra:
    def __init__(self, **extra: object) -> None:
        self.extra = extra

    def update(self, other: typing.Self) -> None:
        self.extra.update(other.extra)

    def apply(self, v: dict[str, typing.Any]) -> dict[str, typing.Any]:
        v.update(self.extra)
        return v


# this is used to detect annotated types in _describe_type
AnnotationType = type(typing.Annotated[object, 'ignored'])


class OpenAPIPath(pydantic.BaseModel):
    path: str = ''
    patterns: dict[str, str] = pydantic.Field(default_factory=dict)


class Parameter(util.FieldOmittingMixin, pydantic.BaseModel):
    """OpenAPI Parameter Object

    https://spec.openapis.org/oas/latest.html#parameter-object
    """

    OMIT_IF_NONE = ('description', 'explode', 'style')
    OMIT_IF_EMPTY = ('schema_',)
    name: str
    in_: str = pydantic.Field(alias='in')
    description: str | None = None
    required: bool = False
    deprecated: bool = False
    schema_: dict[str, object] = pydantic.Field(
        default_factory=dict, alias='schema'
    )
    style: routing.ParameterStyle | None = None
    explode: bool | None = None


ContentType: typing.TypeAlias = str
"""Media type specification"""


class MediaTypeObject(util.FieldOmittingMixin, pydantic.BaseModel):
    """OpenAPI Media Type Object

    https://spec.openapis.org/oas/latest.html#media-type-object
    """

    OMIT_IF_EMPTY = ('schema_',)
    schema_: dict[str, object] = pydantic.Field(
        default_factory=dict, alias='schema'
    )


class ResponseObject(util.FieldOmittingMixin, pydantic.BaseModel):
    """OpenAPI Response Object

    https://spec.openapis.org/oas/latest.html#response-object
    """

    OMIT_IF_EMPTY = ('content', 'headers', 'links')
    description: str
    headers: dict[str, object] = pydantic.Field(default_factory=dict)
    content: dict[ContentType, MediaTypeObject] = pydantic.Field(
        default_factory=dict
    )
    links: dict[str, object] = pydantic.Field(default_factory=dict)


ResponseStatus: typing.TypeAlias = str


class Operation(util.FieldOmittingMixin, pydantic.BaseModel):
    """Describe a single API operation on a path

    https://spec.openapis.org/oas/latest.html#operation-object
    """

    OMIT_IF_NONE = (
        'summary',
        'description',
        'operationId',
        'deprecated',
        'responses',
    )
    OMIT_IF_EMPTY = ('tags', 'responses')

    summary: str | None = None
    description: str | None = None
    operationId: str | None = None  # noqa: N815 -- camelCase ok here
    deprecated: bool | None = None
    tags: list[str] = pydantic.Field(default_factory=list)
    responses: dict[ResponseStatus, ResponseObject] = pydantic.Field(
        default_factory=dict
    )


_OPERATION_NAMES = tuple(m.lower() for m in routing.HTTP_METHOD_NAMES)


class PathDescription(util.FieldOmittingMixin, pydantic.BaseModel):
    """OpenAPI Path Item Object

    https://spec.openapis.org/oas/latest.html#path-item-object
    """

    OMIT_IF_NONE = ('summary', 'description', *_OPERATION_NAMES)
    OMIT_IF_EMPTY = ('parameters',)
    summary: str | None = None
    description: str | None = None
    parameters: list[Parameter] = pydantic.Field(default_factory=list)
    get: Operation | None = None
    head: Operation | None = None
    post: Operation | None = None
    delete: Operation | None = None
    patch: Operation | None = None
    put: Operation | None = None
    options: Operation | None = None

    @property
    def empty(self) -> bool:
        return not any(
            getattr(self, method) is not None for method in _OPERATION_NAMES
        )


def _initialize_type_map(
    m: collections.abc.MutableMapping[type, dict[str, typing.Any]],
) -> None:
    m.update(
        {
            bool: {'type': 'boolean'},
            datetime.date: {'type': 'string', 'format': 'date'},
            datetime.datetime: {'type': 'string', 'format': 'date-time'},
            datetime.time: {'type': 'string', 'format': 'time'},
            datetime.timedelta: {'type': 'string', 'format': 'duration'},
            float: {'type': 'number'},
            int: {'type': 'integer'},
            ipaddress.IPv4Address: {'type': 'string', 'format': 'ipv4'},
            ipaddress.IPv6Address: {'type': 'string', 'format': 'ipv6'},
            str: {'type': 'string'},
            types.NoneType: {'type': 'null'},
            uuid.UUID: {'type': 'string', 'format': 'uuid'},
            yarl.URL: {'type': 'string', 'format': 'uri'},
        }
    )


_simple_type_map = util.ClassMapping[dict[str, typing.Any]](
    initialize_data=_initialize_type_map
)

# NB ... the type of typing.Literal[...] or typing.LiteralString
# cannot be included in a type annotation; however, they are
# matched as "object" instances.
Describable = types.GenericAlias | types.UnionType | type | object
Description = collections.abc.Mapping[str, typing.Any]
MutableDescription = dict[str, typing.Any]


class APIDescription(pydantic.BaseModel):
    """Top-level OpenAPI Object

    Call [describe_api][] to create an instance of the OpenAPI
    specification directly from the application instance.

    https://spec.openapis.org/oas/latest.html#openapi-object
    """

    openapi: str
    info: dict[str, object]
    jsonSchemaDialect: str  # noqa: N815 -- name intentionally camelCased
    servers: list[dict[str, object]]
    paths: dict[str, PathDescription]
    components: dict[str, dict[str, object]]
    tags: list[dict[str, object]]


def describe_api(application: tornado.web.Application) -> APIDescription:
    """Describe an application in an OpenAPI specification"""
    spec = APIDescription(
        openapi='3.1.0',
        info={},
        jsonSchemaDialect='https://spec.openapis.org/oas/3.1/dialect/base',
        servers=[],
        paths={},
        components={},
        tags=[],
    )
    for rule in application.wildcard_router.rules:
        if isinstance(rule, routing.Route | tornado.routing.URLSpec):
            path = _translate_path_pattern(rule.regex)
            description = _describe_path(rule, path)
            if not description.empty:
                spec.paths[path.path] = description
        else:
            warnings.warn(
                f'Rule {rule!r} not processed, unhandled rule '
                f'class {rule.__class__.__name__}',
                stacklevel=2,
            )
    return spec


def describe_type(t: Describable) -> Description:
    """Describe `t` as an OpenAPI schema"""
    return types.MappingProxyType(_describe_type(t))


# mypy gets cranky about the hash-ability of `Describable` or something
# so don't enable the cache while checking types
if not typing.TYPE_CHECKING:  # pragma: nobranch
    describe_type = functools.cache(describe_type)


def _describe_type(t: Describable) -> MutableDescription:
    alias_args = None
    if isinstance(t, types.GenericAlias):
        alias_args, t = typing.get_args(t), typing.get_origin(t)

    t, openapi_extra = _extract_extra(t)

    if isinstance(t, types.UnionType):
        return openapi_extra.apply(
            {
                'anyOf': [
                    _describe_type(union_member)
                    for union_member in typing.get_args(t)
                ]
            }
        )

    if description := _describe_literals(t):
        return openapi_extra.apply(description)

    if not isinstance(t, type):
        raise TypeError(f'Unexpected value of type {type(t)}')  # noqa: TRY003

    if issubclass(t, pydantic.BaseModel):
        return openapi_extra.apply(t.model_json_schema())

    unspecified = {'': object()}
    if (v := _simple_type_map.get(t, unspecified)) is not unspecified:
        return openapi_extra.apply(v.copy())

    if issubclass(t, collections.abc.Mapping):
        return openapi_extra.apply({'type': 'object'})

    if issubclass(t, collections.abc.Collection):
        return openapi_extra.apply(_describe_collection(t, alias_args))

    raise ValueError(f'Unexpected value of type {type(t)}')  # noqa: TRY003


def _describe_literals(t: Describable) -> MutableDescription | None:
    if t is None:
        return {'type': 'null'}
    if t is typing.LiteralString:
        return {'type': 'string'}
    if typing.get_origin(t) is typing.Literal:
        options = []
        for arg in typing.get_args(t):
            options.append(_describe_type(type(arg)))
            options[-1]['const'] = arg
        if len(options) > 1:
            return {'anyOf': options}
        return options[0]
    return None


def _describe_collection(
    t: type,
    alias_args: tuple[Describable, ...] | None,
) -> MutableDescription:
    description: MutableDescription = {'type': 'array'}
    if alias_args:
        if issubclass(t, tuple):
            description['prefixItems'] = [
                _describe_type(item) for item in alias_args
            ]
            description['items'] = False
            description['minItems'] = len(alias_args)
        else:
            if len(alias_args) != 1:
                warnings.warn(
                    f'{len(alias_args)} GenericAlias arguments received,'
                    f' expected exactly one',
                    RuntimeWarning,
                    stacklevel=2,
                )
            description['items'] = _describe_type(alias_args[0])
    return description


def _describe_path(
    route: tornado.routing.URLSpec, path_info: OpenAPIPath
) -> PathDescription:
    logger = util.get_logger_for(_describe_path)
    desc = PathDescription()

    for name, pattern in path_info.patterns.items():
        desc.parameters.append(_describe_path_parameter(name, pattern, route))

    if isinstance(route, routing.Route):
        for method, impl in route.implementations:
            try:
                op = _describe_operation(impl)
            except Exception:
                logger.error('Failed to process %s %r', method, impl)
                raise
            if not isinstance(op, Omit):
                setattr(desc, method.lower(), op)
    elif issubclass(route.handler_class, tornado.web.RequestHandler):
        for method in route.handler_class.SUPPORTED_METHODS:
            impl = getattr(route.handler_class, method.lower())
            if impl != route.handler_class._unimplemented_method:  # noqa: SLF001
                op = _describe_operation(impl)
                if not isinstance(op, Omit):
                    setattr(desc, method.lower(), op)

    return desc


def _describe_path_parameter(
    name: str,
    pattern: str,
    route: tornado.routing.URLSpec,
) -> Parameter:
    defaults = {
        'name': name,
        'in': 'path',
        'required': True,
        'schema': {'type': 'string'},
    }
    if route.kwargs and (
        path_info := route.kwargs.get('path_types', {}).get(name)
    ):
        param = _describe_parameter(path_info, **defaults)
    else:
        param = Parameter.model_validate(defaults)
    if param.schema_.get('type', '') == 'string':
        param.schema_.setdefault('pattern', pattern)
    return param


def _describe_operation(
    func: request_handling.RequestMethod
) -> Operation | Omit:
    if metadata.collect(func, Omit):
        return Omit()

    op = Operation()
    if doc := inspect.getdoc(func):
        _update_operation_from_docstring(op, doc)
    sig = inspect.signature(func)
    if sig.return_annotation is not inspect.Signature.empty:
        resp = ResponseObject(description='default')
        resp.content['application/json'] = MediaTypeObject(
            schema=(_describe_type(sig.return_annotation))
        )
        op.responses['default'] = resp

    for meta in metadata.collect(func, OperationAnnotation):
        op.summary = util.apply_default(op.summary, meta.summary)
        op.description = util.apply_default(op.description, meta.description)
        op.operationId = util.apply_default(op.operationId, meta.operation_id)
        op.tags.extend(meta.tags)

    return op


def _update_operation_from_docstring(op: Operation, docstring: str) -> None:
    lines = docstring.splitlines()
    op.summary = lines.pop(0)
    if lines and lines[0].strip() == '':
        op.description = '\n'.join(lines[1:])


def _describe_parameter(param_info: object, **defaults: object) -> Parameter:
    alternatives = []
    param = Parameter.model_validate(defaults)
    for item in metadata.extract(param_info):
        if isinstance(item, SchemaExtra):
            param.schema_.update(item.extra)
        elif isinstance(item, routing.ParameterAnnotation):
            alternatives.append(item.schema_)
            param.description = util.apply_default(
                param.description, item.description
            )
    if alternatives:
        if len(alternatives) == 1:
            param.schema_.update(alternatives[0])
        else:
            param.schema_ = {'oneOf': alternatives}
    return param


def _extract_extra(t: Describable) -> tuple[Describable, SchemaExtra]:
    unwrapped = t
    extra = SchemaExtra()
    if util.is_annotated(t):
        unwrapped, md = util.unwrap_annotation(t)
        for meta in md:
            if isinstance(meta, SchemaExtra):
                extra.update(meta)
    return unwrapped, extra


def _translate_path_pattern(pattern: re.Pattern[str]) -> OpenAPIPath:
    r"""Translate a path regex into an OpenAPI path

    This function tries to parse a Tornado path expression into a
    matching OpenAPI operation path while capturing the underlying
    regular expression patterns. Not all patterns supported by
    the [re][] module implements. If you stumble into an unsupported
    expression, it will be left as-is and a warning is issued.

    >>> info = _translate_path_pattern(re.compile(
    ...    r'/projects/(?P<id>[1-9]\d*)/facts/(?P<fact_id>\d+)'))
    >>> info.path
    '/projects/{id}/facts/fact_id'
    >>> info.patterns['id']
    '[1-9]\d*'
    >>> info.patterns['fact_id']
    '\d+'

    """
    working = pattern.pattern.removesuffix('$')

    patterns: dict[str, str] = {}
    while match := re.search(r'\((?P<part>\?[^()]*)\)', working):
        quantifier, value = match['part'][:2], match['part'][2:]
        start, end = match.span()
        if quantifier == '?P':
            if match := re.search(r'<(?P<name>[^>]+)>', value):
                value = value[match.end() :]
                patterns[match['name']] = value
                value = f'{{{match["name"]}}}'
            elif value.startswith('='):
                value = f'{{{value[1:]}}}'
            else:  # pragma: nocover
                raise RuntimeError('Invalid regular expression')  # noqa: TRY003
        elif quantifier == '?#':
            value = ''
        elif quantifier == '?:':
            # Escape the parens here to avoid the top-level
            # matching pattern from reprocessing this value.
            value = f'\x01{value}\x02'
        else:
            warnings.warn(
                f'{quantifier!r} is not implemented and will result in '
                f'an invalid OpenAPI path expression',
                stacklevel=2,
            )
        working = working[:start] + value + working[end:]

    # reverse the parenthesis escaping in the path and every
    # parsed expression
    working = working.replace('\x01', '(').replace('\x02', ')')
    for var, patn in patterns.items():
        patterns[var] = patn.replace('\x01', '(').replace('\x02', ')')

    return OpenAPIPath(
        patterns=patterns,
        path='/' if working == '/?' else working.removesuffix('/?'),
    )


@typing.overload
def describe_operation(
    f: request_handling.RequestMethod, /, **kwargs: str | bool | list[str]
) -> request_handling.RequestMethod:
    ...


@typing.overload
def describe_operation(
    **kwargs: str | bool | list[str]
) -> typing.Callable[
    [request_handling.RequestMethod], request_handling.RequestMethod
]:
    ...


def describe_operation(
    op: request_handling.RequestMethod | None = None,
    /,
    **kwargs: str | bool | list[str],
) -> (
    request_handling.RequestMethod
    | typing.Callable[
        [request_handling.RequestMethod], request_handling.RequestMethod
    ]
):
    if not kwargs:
        raise errors.InvalidDescribeOperationError()

    anno = OperationAnnotation.model_validate(kwargs)

    def outer(
        f: request_handling.RequestMethod
    ) -> request_handling.RequestMethod:
        return metadata.append(f, anno)

    if op is not None:
        return outer(op)

    return outer
