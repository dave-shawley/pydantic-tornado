import collections.abc
import contextlib
import datetime
import inspect
import ipaddress
import re
import typing
import uuid

import tornado.routing
import tornado.web

from pydantictornado import errors, request_handling, util

BOOLEAN_TRUE_STRINGS: set[str] = set()
"""String values that are parsed as `True` for boolean parameters"""

BOOLEAN_FALSE_STRINGS: set[str] = set()
"""String values that are parsed as `False` for boolean parameters"""

_HTTP_METHOD_NAMES = frozenset(tornado.web.RequestHandler.SUPPORTED_METHODS)

PathConverter = typing.Callable[[str], typing.Any]


def _initialize_converters(
    m: collections.abc.MutableMapping[type, PathConverter]
) -> None:
    m.update(
        {
            bool: lambda s: _convert_bool(s),
            float: lambda s: float(s),
            int: lambda s: int(s, 10),
            str: lambda s: s,
            uuid.UUID: lambda s: uuid.UUID(s),
            datetime.date: lambda s: _parse_datetime(s).date(),
            datetime.datetime: lambda s: _parse_datetime(s),
            ipaddress.IPv4Address: lambda s: ipaddress.IPv4Address(s),
            ipaddress.IPv6Address: lambda s: ipaddress.IPv6Address(s),
        }
    )


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
        return _converters[param.annotation]
    except KeyError:
        raise errors.UnroutableParameterTypeError(param.annotation) from None


def _parse_datetime(value: str) -> datetime.datetime:
    formats = ['%Y', '%Y-%m', '%Y%m']
    with contextlib.suppress(ValueError):
        then = datetime.datetime.fromisoformat(value)
        if not then.tzinfo:
            then = then.replace(tzinfo=datetime.UTC)
        return then
    for fmt in formats:
        with contextlib.suppress(ValueError):
            return datetime.datetime.strptime(value, fmt).replace(
                tzinfo=datetime.UTC
            )
    raise errors.ValueParseError(value, datetime.datetime)


def _convert_bool(value: str) -> bool:
    if value in BOOLEAN_TRUE_STRINGS:
        return True
    if value in BOOLEAN_FALSE_STRINGS:
        return False
    try:
        int_value = int(value, base=10)
    except (TypeError, ValueError):
        raise errors.ValueParseError(value, int) from None
    return bool(int_value)
