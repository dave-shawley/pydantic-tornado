import collections.abc
import contextlib
import datetime
import ipaddress
import logging
import typing
import uuid

import pydantic
import yarl

from pydantictornado import errors

AnyCallable: typing.TypeAlias = typing.Callable[..., object | None]
AnyType = type
DefaultType = typing.TypeVar('DefaultType')

T = typing.TypeVar('T')
DataInitializer = typing.Callable[
    [collections.abc.MutableMapping[AnyType, T]], None
]


class Unspecified:
    """Simple type that is never true"""

    def __bool__(self) -> bool:
        return False


BOOLEAN_TRUE_STRINGS: set[str] = set()
"""String values that are parsed as `True` for boolean parameters"""

BOOLEAN_FALSE_STRINGS: set[str] = set()
"""String values that are parsed as `False` for boolean parameters"""

UNSPECIFIED = Unspecified()


def get_logger_for(obj: object) -> logging.Logger:
    """Retrieve a logger associated with `obj.__class__`"""
    cls = obj.__class__
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


class ClassMapping(collections.abc.MutableMapping[AnyType, T]):
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
        data: collections.abc.Mapping[AnyType, T] | object = UNSPECIFIED,
        *,
        initialize_data: DataInitializer[T] | None = None,
    ) -> None:
        self._data: list[tuple[AnyType, T]] = []
        self._cache: dict[AnyType, T] = {}
        self._initialize_data = initialize_data
        if data is not UNSPECIFIED:
            data = typing.cast(collections.abc.Mapping[AnyType, T], data)
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
    def _probe(self, item: AnyType) -> int:
        ...

    @typing.overload
    def _probe(
        self, item: AnyType, *, default: DefaultType
    ) -> int | DefaultType:
        ...

    def _probe(
        self,
        item: AnyType,
        *,
        default: DefaultType | Unspecified = UNSPECIFIED,
    ) -> int | DefaultType:
        index = 0
        item = strip_annotation(item)
        for base_cls, _ in self._data:
            try:
                is_subclass = issubclass(item, base_cls)
            except TypeError as error:
                get_logger_for(self).error(
                    'issubclass() failed for item %r and base %r: %s',
                    item,
                    base_cls,
                    error,
                )
                raise errors.TypeRequiredError(item) from None
            else:
                if is_subclass:
                    return index
            index += 1
        if not isinstance(default, Unspecified):
            return default
        raise KeyError(item)

    def __getitem__(self, item: AnyType) -> T:
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

    def __setitem__(self, key: AnyType, value: T) -> None:
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

    def __delitem__(self, key: AnyType) -> None:
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

    def __iter__(self) -> typing.Iterator[AnyType]:
        return iter(t[0] for t in self._data)


@typing.runtime_checkable
class HasIsoFormat(typing.Protocol):
    def isoformat(self, spec: str = ...) -> str:
        ...


def json_serialize_hook(
    obj: object
) -> bool | float | int | str | dict[str, object]:
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

    This function uses the [BOOLEAN_TRUE_STRINGS][] and
    [BOOLEAN_FALSE_STRINGS][] constants to convert `value`
    to a `bool`. If there is not a direct string match, it
    tries to convert `value` to an int and then casts that
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


def strip_annotation(t: AnyType) -> AnyType:
    """Remove annotations from `t`"""
    return unwrap_annotation(t)[0]


@typing.overload
def unwrap_annotation(v: type) -> tuple[type, tuple[object, ...]]:
    ...


@typing.overload
def unwrap_annotation(v: AnyCallable) -> tuple[AnyCallable, tuple[()]]:
    ...


def unwrap_annotation(
    v: type | AnyCallable
) -> tuple[type | AnyCallable, tuple[object, ...]]:
    """Separate the origin value and metadata if `v` is annotated"""
    if typing.get_origin(v) == typing.Annotated:
        # NB ... typing.get_origin() returns None when given a
        # non-type value, so we WILL NOT GET HERE for AnyCallable
        return v.__origin__, v.__metadata__  # type: ignore[union-attr]
    return v, ()


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
