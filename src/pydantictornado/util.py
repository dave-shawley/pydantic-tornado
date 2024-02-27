import collections.abc
import datetime
import ipaddress
import logging
import typing
import uuid

import yarl

AnyType = type
T = typing.TypeVar('T')
DataInitializer = typing.Callable[
    [collections.abc.MutableMapping[AnyType, T]], None
]

UNSPECIFIED = object()


def get_logger_for(obj: object) -> logging.Logger:
    """Retrieve a logger associated with `obj.__class__`"""
    cls = obj.__class__
    return logging.getLogger(cls.__module__).getChild(cls.__name__)


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

    def _probe(self, item: AnyType) -> int | None:
        index = 0
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
                raise
            else:
                if is_subclass:
                    return index
            index += 1
        return None

    def __getitem__(self, item: AnyType) -> T:
        if item in self._cache:
            return self._cache[item]

        if (index := self._probe(item)) is not None:
            base_cls, coercion = self._data[index]
            self._cache[item] = coercion
            return coercion

        raise KeyError(item)

    def __setitem__(self, key: AnyType, value: T) -> None:
        if not isinstance(key, type):
            t = type(key)  # type: ignore[unreachable]
            msg = f'{key} is a {t.__module__}.{t.__name__}, not a type'
            raise TypeError(msg)
        self._cache.clear()
        if (index := self._probe(key)) is not None:
            self._data.insert(index, (key, value))
        else:
            self._data.append((key, value))
        self._cache[key] = value

    def __delitem__(self, key: AnyType) -> None:
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


class NotSerializableError(TypeError):
    def __init__(self, obj: object) -> None:
        super().__init__(
            f'Object of type {obj.__class__.__name__} is not serializable'
        )


def json_serialize_hook(obj: object) -> bool | float | int | str:
    if isinstance(obj, HasIsoFormat):
        return obj.isoformat()
    if isinstance(
        obj,
        ipaddress.IPv4Address | ipaddress.IPv6Address | uuid.UUID | yarl.URL,
    ):
        return str(obj)
    if isinstance(obj, datetime.timedelta):
        return _format_isoduration(obj.total_seconds())

    raise NotSerializableError(obj)


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
