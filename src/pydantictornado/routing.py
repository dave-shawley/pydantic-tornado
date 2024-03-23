import collections.abc
import datetime
import enum
import functools
import inspect
import ipaddress
import re
import typing
import uuid

import pydantic
import tornado.routing
import tornado.web

from pydantictornado import errors, request_handling, util

_HTTP_METHOD_NAMES = frozenset(tornado.web.RequestHandler.SUPPORTED_METHODS)

PathConverter = typing.Callable[[str], typing.Any]


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


def _initialize_converters(
    m: collections.abc.MutableMapping[type, PathConverter]
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
        uuid.UUID: uuid.UUID,
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
    }
    m.update(mapping)  # type: ignore[arg-type]


_converters = util.ClassMapping[PathConverter](
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
            if name.upper() in _HTTP_METHOD_NAMES:
                if not inspect.iscoroutinefunction(value):
                    raise errors.CoroutineRequiredError(value)
                self._implementations[name.upper()] = value
            target_kwargs[name] = value

        if not self._implementations:
            raise errors.NoHttpMethodsDefinedError

        if isinstance(pattern, str):
            pattern = re.compile(pattern.removesuffix('$') + '$')

        path_types: dict[str, PathConverter] = {}
        path_groups = pattern.groupindex
        if path_groups:
            target_kwargs['path_types'] = path_types
        for impl in self._implementations.values():
            self._process_path_parameters(impl, pattern, path_types)

        super().__init__(
            pattern,
            handler=request_handling.RequestHandler,
            kwargs=target_kwargs,
        )

    @staticmethod
    def _process_path_parameters(
        impl: request_handling.RequestMethod,
        pattern: re.Pattern[str],
        path_types: dict[str, PathConverter],
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


def _build_coercion(param: inspect.Parameter) -> PathConverter:
    try:
        coercion = _converters[param.annotation]
    except KeyError:
        raise errors.UnroutableParameterTypeError(param.annotation) from None
    else:
        if typing.get_origin(coercion) == typing.Annotated:
            origin = coercion.__origin__  # type: ignore[attr-defined]
            metadata = (
                *coercion.__metadata__,  # type: ignore[attr-defined]
                *getattr(param.annotation, '__metadata__', ()),
            )
            return typing.cast(
                PathConverter, typing.Annotated[origin, *metadata]
            )
        return coercion
