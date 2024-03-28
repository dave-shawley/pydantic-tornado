import collections.abc
import datetime
import functools
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

from pydantictornado import routing, util


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
    name: str
    in_: str = pydantic.Field(alias='in')
    description: str | None = None
    required: bool = False
    deprecated: bool = False
    schema_: dict[str, object] | None = pydantic.Field(None, alias='schema')
    style: routing.ParameterStyle | None = None
    explode: bool | None = None


class PathDescription(util.FieldOmittingMixin, pydantic.BaseModel):
    """OpenAPI Path Item Object

    https://spec.openapis.org/oas/latest.html#path-item-object
    """

    OMIT_IF_NONE = ('summary', 'description')
    OMIT_IF_EMPTY = ('parameters',)
    summary: str | None = None
    description: str | None = None
    parameters: list[Parameter] = pydantic.Field(default_factory=list)


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
            spec.paths[path.path] = _describe_path(rule, path)
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
        raise NotImplementedError('Mapping not implemented')

    if issubclass(t, collections.abc.Collection):
        return openapi_extra.apply(_describe_collection(t, alias_args))

    raise ValueError


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
    desc = PathDescription()
    for name, pattern in path_info.patterns.items():
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
        desc.parameters.append(param)
    return desc


def _describe_parameter(param_info: object, **defaults: object) -> Parameter:
    param = Parameter.model_validate(defaults)
    for item in getattr(param_info, '__metadata__', []):
        if isinstance(item, SchemaExtra):
            param.schema_.update(item.extra)
        elif isinstance(item, routing.ParameterAnnotation):
            if 'oneOf' in item.schema_ or 'allOf' in item.schema_:
                param.schema_.pop('type', None)
            param.schema_.update(item.schema_)
            param.description = param.description or item.description
    return param


def _extract_extra(t: Describable) -> tuple[Describable, SchemaExtra]:
    unwrapped = t
    extra = SchemaExtra()

    if isinstance(t, AnnotationType):
        try:
            metadata: list[object] = t.__metadata__  # type: ignore[attr-defined]
            unwrapped = t.__origin__  # type: ignore[attr-defined]
        except AttributeError:  # pragma: nocover -- being overly cautious here
            pass
        else:
            for meta in metadata:
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
