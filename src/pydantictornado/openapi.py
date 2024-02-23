import collections.abc
import datetime
import functools
import ipaddress
import types
import typing
import uuid
import warnings

import pydantic
import yarl

from pydantictornado import util


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


def _initialize_type_map(
    m: collections.abc.MutableMapping[type, dict[str, typing.Any]]
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
