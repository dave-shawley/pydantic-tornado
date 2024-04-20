import collections.abc
import contextlib
import datetime
import functools
import inspect
import ipaddress
import logging
import types
import typing
import uuid

import pydantic
import yarl

from pydantictornado import errors

AnyCallable: typing.TypeAlias = typing.Callable[..., object | None]
SomeCallable = typing.TypeVar(
    'SomeCallable', bound=typing.Callable[..., typing.Any]
)
DefaultType = typing.TypeVar('DefaultType')

T = typing.TypeVar('T')
SomeType = typing.TypeVar('SomeType', bound=type)
DataInitializer = typing.Callable[
    [collections.abc.MutableMapping[type, T]], None
]


class Unspecified:
    """Simple type that is never true"""

    def __bool__(self) -> bool:  # pragma: nocover
        # note that this method may or may not be necessary based
        # on the implementation ... I'm leaving it here to avoid
        # adding and removing it constantly
        return False


BOOLEAN_TRUE_STRINGS: set[str] = set()
"""String values that are parsed as `True` for boolean parameters"""

BOOLEAN_FALSE_STRINGS: set[str] = set()
"""String values that are parsed as `False` for boolean parameters"""

UNSPECIFIED = Unspecified()
"""An unspecified value when `None` is not appropriate"""


def apply_default(
    value: T,
    default: T | Unspecified | None | types.EllipsisType,
) -> T:
    """Use a default value when appropriate

    Note that this function will return `value` if `default`
    is `None`, `...`, or an `Unspecified` instance. This matters
    when *both* `value` and `default` are one of the "defaulted"
    values (eg, `None`, `...`, `UNSPECIFIED`).
    """
    if default in (UNSPECIFIED, None, ...) or isinstance(default, Unspecified):
        return value
    if value in (UNSPECIFIED, None, ...) or isinstance(value, Unspecified):
        return typing.cast(T, default)
    return value


@functools.cache
def get_logger_for(obj: object) -> logging.Logger:
    """Retrieve a logger for obj.__class__

    >>> class C:
    ...     def __init__(self):
    ...         self.logger = get_logger_for(self)
    >>> c = C()
    >>> c.logger.name
    'util.C'

    """
    if isinstance(obj, types.FunctionType):
        return logging.getLogger(obj.__module__).getChild(obj.__name__)
    cls = obj if isinstance(obj, type) else type(obj)
    return logging.getLogger(cls.__module__).getChild(cls.__name__)


class FieldOmittingMixin(pydantic.BaseModel):
    """Mix this into pydantic models to omit `None` fields

    The fields named in `OMIT_IF_NONE` will be ignored during
    serialization if their value is `None`.

    The fields named in `OMIT_IF_EMPTY` will be ignored during
    serialization if their value is *empty*. This is only relevant
    for instances that implement [collections.abc.Sized][].

    """

    OMIT_IF_EMPTY: typing.ClassVar[tuple[str, ...]] = ()
    OMIT_IF_NONE: typing.ClassVar[tuple[str, ...]] = ()

    @pydantic.model_serializer(mode='wrap')
    def _omit_fields(
        self,
        handler: typing.Callable[
            [typing.Self, pydantic.SerializationInfo], dict[str, object]
        ],
        info: pydantic.SerializationInfo,
    ) -> dict[str, object]:
        result = handler(self, info)
        for name in self.model_fields:
            value = result.get(name, None)
            empty = isinstance(value, collections.abc.Sized) and not len(value)
            if (name in self.OMIT_IF_NONE and value is None) or (
                name in self.OMIT_IF_EMPTY and empty
            ):
                result.pop(name)
        return result


class ClassMapping(collections.abc.MutableMapping[type, T]):
    """Map types to another value

    This class maps classes to another value respecting
    subclass identity. For example, the following maps
    python types to OpenAPI schema descriptions.

    >>> mapping = ClassMapping()
    >>> mapping.update({
    ...     int: {'type': 'integer'},
    ...     float: {'type': 'number'},
    ...     list: {'type': 'array'},
    ...     str: {'type': 'string'},
    ... })
    >>> mapping[int]
    {'type': 'integer'}

    Note that this class expects *types* and not type
    aliases so adding things like ``list[int]`` will fail.

    >>> mapping[list[int]] = 'whatever'
    Traceback (most recent call last):
      ...
    TypeError: list[int] is a types.GenericAlias, not a type

    :param DataInitializer initialize_data: function to call
        to initialize or re-initialize the maps data

    """

    def __init__(
        self,
        data: collections.abc.Mapping[type, T] | object = UNSPECIFIED,
        *,
        initialize_data: DataInitializer[T] | None = None,
    ) -> None:
        self.logger = logging.getLogger(__package__).getChild(
            self.__class__.__name__
        )
        self._data: list[tuple[type, T]] = []
        self._cache: dict[type, T] = {}
        self._initialize_data = initialize_data
        if data is not UNSPECIFIED:
            data = typing.cast(collections.abc.Mapping[type, T], data)
            for key, value in data.items():
                self[key] = value
        if self._initialize_data is not None:
            self.rebuild()

    def rebuild(self) -> None:
        """Clear the mapping and re-initialize it"""
        self._data.clear()
        self._cache.clear()
        if self._initialize_data is not None:
            self._initialize_data(self)
            self.populate_cache()

    def populate_cache(self) -> None:
        """Populate the internal cache for every mapped type"""
        for key, _ in self._data:
            self.__getitem__(key)

    @typing.overload
    def _probe(self, item: type) -> int:
        ...

    @typing.overload
    def _probe(self, item: type, *, default: DefaultType) -> int | DefaultType:
        ...

    def _probe(
        self,
        item: type,
        *,
        default: DefaultType | Unspecified = UNSPECIFIED,
    ) -> int | DefaultType:
        item = strip_annotation(item)
        for index, data in enumerate(self._data):
            base_cls, _ = data
            try:
                is_subclass = issubclass(item, base_cls)
            except TypeError as error:
                self.logger.error(
                    'issubclass() failed for item %r and base %r: %s',
                    item,
                    base_cls,
                    error,
                )
                raise errors.TypeRequiredError(item) from None
            else:
                if is_subclass:
                    return index
        if not isinstance(default, Unspecified):
            return default
        raise KeyError(item)

    def __getitem__(self, item: type) -> T:
        item = strip_annotation(item)
        try:
            coercion = self._cache[item]
        except KeyError:
            index = self._probe(item)
            base_cls, coercion = self._data[index]
            self._cache[item] = coercion
            return coercion
        except TypeError:
            raise errors.TypeRequiredError(item) from None
        else:
            return coercion

    def __setitem__(self, key: type, value: T) -> None:
        key = strip_annotation(key)
        if not isinstance(key, type):
            raise errors.TypeRequiredError(key)
        self._cache.clear()
        not_found = object()
        if (index := self._probe(key, default=not_found)) is not not_found:
            self._data.insert(index, (key, value))  # type: ignore[arg-type]
        else:
            self._data.append((key, value))
        self._cache[key] = value

    def __delitem__(self, key: type) -> None:
        key = strip_annotation(key)
        self._cache.clear()
        for idx, (base_cls, _) in enumerate(self._data):
            if base_cls is key:
                del self._data[idx]
                break
        else:
            raise IndexError(key)

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> typing.Iterator[type]:
        return iter(t[0] for t in self._data)


@typing.runtime_checkable
class HasIsoFormat(typing.Protocol):
    """Runtime checkable protocol that detects the isoformat method"""

    def isoformat(self, spec: str = ...) -> str:
        ...


def json_serialize_hook(
    obj: object,
) -> bool | float | int | str | dict[str, object]:
    """Standard `default` function passed to json.dump

    This function is used to serialize a number of non-standard
    objects in response values.

    | Type                      | Description                     |
    | ------------------------- | ------------------------------- |
    | has attribute isoformat   | `obj.isoformat()`               |
    | [ipaddress.IPv4Address][] | `str(obj)`                      |
    | [ipaddress.IPv6Address][] | `str(obj)`                      |
    | [uuid.UUID][]             | `str(obj)`                      |
    | [yarl.URL][]              | `str(obj)`                      |
    | [datetime.timedelta][]    | serialized as ISO-8601 duration |
    | Pydantic models           | `obj.model_dump(by_alias=True)` |

    """
    if isinstance(obj, HasIsoFormat):
        return obj.isoformat()
    if isinstance(
        obj,
        ipaddress.IPv4Address | ipaddress.IPv6Address | uuid.UUID | yarl.URL,
    ):
        return str(obj)
    if isinstance(obj, datetime.timedelta):
        return _format_isoduration(obj.total_seconds())
    if isinstance(obj, pydantic.BaseModel):
        return obj.model_dump(by_alias=True)

    raise errors.NotSerializableError(obj)


def convert_bool(value: str) -> bool:
    """Convert `value` into a Boolean based on configuration

    This function uses the [pydantictornado.util.BOOLEAN_TRUE_STRINGS][]
    and [pydantictornado.util.BOOLEAN_FALSE_STRINGS][] constants to
    convert `value` to a `bool`. If there is not a direct string
    match, it tries to convert `value` to an int and then casts that
    to a Boolean value.

    Raises [pydantictornado.errors.ValueParseError][] if it
    cannot convert `value` to a Boolean value.
    """
    if value in BOOLEAN_TRUE_STRINGS:
        return True
    if value in BOOLEAN_FALSE_STRINGS:
        return False
    try:
        int_value = int(value, base=10)
    except (TypeError, ValueError):
        raise errors.ValueParseError(value, int) from None
    return bool(int_value)


def parse_datetime(value: str) -> datetime.datetime:
    """Parse `value` into a datetime according to ISO-8601

    Uses [datetime.datetime.fromisoformat][] to parse `value`.
    If that fails, then the shortened date forms are used.

    Raises [pydantictornado.errors.ValueParseError][] if it
    cannot convert `value` to a Boolean value.
    """
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


def parse_date(value: str) -> datetime.date:
    """Parses `value` as a datetime.datetime and discards the time"""
    return parse_datetime(value).date()


def strip_annotation(t: SomeType) -> SomeType:
    """Remove annotations from `t`"""
    return unwrap_annotation(t)[0]


def unwrap_annotation(v: SomeType) -> tuple[SomeType, tuple[object, ...]]:
    """Separate the origin value and metadata if `v` is annotated"""
    if typing.get_origin(v) == typing.Annotated:
        # NB ... typing.get_origin() returns None when given a
        # non-type value, so we ONLY get here for annotated type
        # values which always have __origin__ and __metadata__
        # attributes
        return v.__origin__, v.__metadata__  # type: ignore[attr-defined]
    return v, ()


def is_coroutine_function(
    obj: object | type,
) -> typing.TypeGuard[typing.Callable[..., typing.Awaitable[typing.Any]]]:
    """inspect.iscoroutinefunction that unwraps annotations first"""
    return inspect.iscoroutinefunction(obj)


def clone_function(f: SomeCallable) -> SomeCallable:
    """Create a new function instance that shares code with `f`"""
    new_func = types.FunctionType(
        f.__code__,
        f.__globals__,
        f.__name__,
        f.__defaults__,
        f.__closure__,
    )
    functools.update_wrapper(new_func, f)
    return typing.cast(SomeCallable, new_func)


def _format_isoduration(seconds: float) -> str:
    if seconds == 0.0:  # noqa: PLR2004 -- magic value okay here
        return 'PT0S'

    parts = []
    rem, prec = seconds, 6
    for spec, reduction in [('S', 60), ('M', 60), ('H', 24)]:
        rem, value = divmod(rem, reduction)
        if value:
            value = round(value, prec) if prec else int(value)
            parts.append(f'{value}{spec}')
        prec = 0

    parts.append('T')
    if rem:
        parts.append(f'{int(rem)}D')
    parts.append('P')

    return ''.join(reversed(parts))
