import collections.abc
import contextlib
import datetime
import enum
import functools
import inspect
import ipaddress
import re
import types
import typing
import uuid

import pydantic
import tornado.routing
import tornado.web

from pydantictornado import errors, request_handling, util

HTTP_METHOD_NAMES = frozenset(tornado.web.RequestHandler.SUPPORTED_METHODS)
"""Supported HTTP methods"""


class _PathConverter(typing.Protocol):
    """Path parameter that has been annotated with metadata

    This is used to simplify type checking since typing.Annotated
    is not *really* a type though it guarantees that two properties
    are present.
    """

    def __call__(self, value: str) -> object:
        ...

    @property
    def __origin__(self) -> typing.Self:
        ...

    @property
    def __metadata__(self) -> tuple[object, ...]:
        ...

    def __hash__(self) -> int:
        ...


class _UnionPathConverter:
    """A PathConverter that implements first-match for union types"""

    def __init__(self, converters: typing.Iterable[_PathConverter]) -> None:
        self.converters = list(converters)

    def __call__(self, value: str, /) -> object:
        for converter in self.converters:
            # uuid.UUID(1.23) raises AttributeError
            # uuid.UUID(None) raises TypeError
            # float('foo')    raises ValueError
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                return converter(value)
        raise errors.ValueParseError(value, self)

    def __eq__(self, other: object) -> bool:
        if other is self:
            return True
        if isinstance(other, self.__class__):
            return all(
                _compare(util.strip_annotation(x), util.strip_annotation(y))
                for x, y in zip(self.converters, other.converters, strict=True)
            )
        return NotImplemented

    def __str__(self) -> str:
        return '{}([{}])'.format(
            self.__class__.__name__,
            ', '.join(str(util.strip_annotation(c)) for c in self.converters),
        )


@functools.cache
def _compare(a: object, b: object) -> bool:
    if a is b:
        return True
    if isinstance(a, functools.partial) and isinstance(b, functools.partial):
        # special handling is required for functools.partial instances
        # https://github.com/python/cpython/issues/65329
        return (
            a.func == b.func and a.args == b.args and a.keywords == b.keywords
        )
    return a == b


def _build_coercion(param: inspect.Parameter) -> _PathConverter:
    coercion: _UnionPathConverter | _PathConverter
    param_type, param_metadata = util.unwrap_annotation(param.annotation)
    options = typing.get_args(param_type)
    if options:
        coercion = _UnionPathConverter(
            _lookup_coercion(cls) for cls in options
        )
        coercion = _annotate_union_parameter(coercion)
    else:
        coercion = _lookup_coercion(param.annotation)

    if param_metadata:  # combine annotations from signature and coercion
        origin, coercion_metadata = util.unwrap_annotation(coercion)
        coercion = typing.cast(
            _PathConverter,
            typing.Annotated[origin, *param_metadata, *coercion_metadata],
        )

    return coercion


class ParameterStyle(enum.StrEnum):
    """Parameter encoding style

    https://spec.openapis.org/oas/latest.html#style-values
    """

    MATRIX = 'matrix'
    LABEL = 'label'
    FORM = 'form'
    SIMPLE = 'simple'
    SPACE_DELIMITED = 'spaceDelimited'
    PIPE_DELIMITED = 'pipeDelimited'
    DEEP_OBJECT = 'deepObject'


class ParameterAnnotation(pydantic.BaseModel):
    """Add extended OpenAPI information to parmeter types

    https://spec.openapis.org/oas/latest.html#parameter-object
    """

    description: str | None = None
    schema_: dict[str, object] = pydantic.Field(default_factory=dict)
    style: ParameterStyle | None = None
    explode: bool | None = None


class _UUID(uuid.UUID):
    """Wrapper class to work around defect in uuid.UUID

    This works around a defect in annotation process of
    immutable values that will be fixed in 3.12.3.

    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        if len(args) == 1 and isinstance(args[0], uuid.UUID):
            super().__init__(int=args[0].int)
        else:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def __setattr__(self, key: str, value: object) -> None:
        # See https://github.com/python/cpython/issues/115165
        if key == '__orig_class__':
            return
        super().__setattr__(key, value)


def _initialize_converters(
    m: collections.abc.MutableMapping[type, _PathConverter],
) -> None:
    mapping = {
        bool: typing.Annotated[
            util.convert_bool,
            ParameterAnnotation(schema_={'type': 'boolean'}),
        ],
        float: typing.Annotated[
            float, ParameterAnnotation(schema_={'type': 'float'})
        ],
        int: typing.Annotated[
            functools.partial(int, base=10),
            ParameterAnnotation(schema_={'type': 'int'}),
        ],
        str: typing.Annotated[
            lambda s: s, ParameterAnnotation(schema_={'type': 'string'})
        ],
        uuid.UUID: typing.Annotated[
            _UUID,
            ParameterAnnotation(schema_={'type': 'string', 'format': 'uuid'}),
        ],
        datetime.date: typing.Annotated[
            util.parse_date,
            ParameterAnnotation(schema_={'type': 'string', 'format': 'date'}),
        ],
        datetime.datetime: typing.Annotated[
            util.parse_datetime,
            ParameterAnnotation(
                schema_={'type': 'string', 'format': 'date-time'}
            ),
        ],
        ipaddress.IPv4Address: typing.Annotated[
            ipaddress.IPv4Address,
            ParameterAnnotation(schema_={'type': 'string', 'format': 'ipv4'}),
        ],
        ipaddress.IPv6Address: typing.Annotated[
            ipaddress.IPv6Address,
            ParameterAnnotation(schema_={'type': 'string', 'format': 'ipv6'}),
        ],
        types.NoneType: typing.Annotated[
            lambda _: None,
            ParameterAnnotation(schema_={'type': 'null'}),
        ],
    }
    m.update(mapping)  # type: ignore[arg-type]


_converters = util.ClassMapping[_PathConverter](
    initialize_data=_initialize_converters
)


class Route(tornado.routing.URLSpec):
    _implementations: dict[str, request_handling.RequestMethod]

    def __init__(
        self,
        pattern: str | re.Pattern[str],
        **kwargs: request_handling.RequestMethod | object,
    ) -> None:
        self._implementations = {}

        target_kwargs = {}
        for name, value in kwargs.items():
            if name.upper() in HTTP_METHOD_NAMES:
                if not util.is_coroutine_function(value):
                    raise errors.CoroutineRequiredError(value)
                self._implementations[name.upper()] = value
            target_kwargs[name] = value

        if not self._implementations:
            raise errors.NoHttpMethodsDefinedError

        if isinstance(pattern, str):
            pattern = re.compile(pattern.removesuffix('$') + '$')

        path_types: dict[str, _PathConverter] = {}
        path_groups = pattern.groupindex
        if path_groups:
            target_kwargs['path_types'] = path_types
        for impl in self._implementations.values():
            _process_path_parameters(impl, pattern, path_types)

        super().__init__(
            pattern,
            handler=request_handling.RequestHandler,
            kwargs=target_kwargs,
        )

    @property
    def implementations(
        self,
    ) -> typing.Iterator[tuple[str, request_handling.RequestMethod]]:
        yield from self._implementations.items()


def _process_path_parameters(
    impl: request_handling.RequestMethod,
    pattern: re.Pattern[str],
    path_types: dict[str, _PathConverter],
) -> None:
    sig = inspect.signature(impl)
    for name, param in sig.parameters.items():
        if name in pattern.groupindex:
            coercion = _build_coercion(param)
            try:
                existing = path_types[name]
            except KeyError:
                path_types[name] = coercion
            else:
                if existing != coercion:
                    raise errors.PathTypeMismatchError(pattern, name)


def _lookup_coercion(cls: type) -> _PathConverter:
    cls, additional_metadata = util.unwrap_annotation(cls)
    try:
        coercion = _converters[cls]
    except KeyError:
        raise errors.UnroutableParameterTypeError(cls) from None
    else:
        return typing.cast(
            _PathConverter,
            typing.Annotated[
                coercion.__origin__,
                *coercion.__metadata__,
                *additional_metadata,
            ],
        )


def _annotate_union_parameter(coercion: _UnionPathConverter) -> _PathConverter:
    one_of: list[dict[str, object]] = []
    for alternative in coercion.converters:
        one_of.extend(
            meta.schema_
            for meta in getattr(alternative, '__metadata__', ())
            if isinstance(meta, ParameterAnnotation)
        )
    return typing.cast(
        _PathConverter,
        typing.Annotated[
            coercion, ParameterAnnotation(schema_={'oneOf': one_of})
        ],
    )
