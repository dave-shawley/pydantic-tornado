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
    if isinstance(t, types.UnionType):
        return {
            'anyOf': [
                _describe_type(union_member)
                for union_member in typing.get_args(t)
            ]
        }

    if description := _describe_literals(t):
        return description

    if not isinstance(t, type):
        raise TypeError(f'Unexpected value of type {type(t)}')  # noqa: TRY003

    if issubclass(t, pydantic.BaseModel):
        return t.model_json_schema()

    unspecified = {'': object()}
    if (v := _simple_type_map.get(t, unspecified)) is not unspecified:
        return v.copy()

    if issubclass(t, collections.abc.Mapping):
        raise NotImplementedError('Mapping not implemented')

    if issubclass(t, collections.abc.Collection):
        return _describe_collection(t, alias_args)

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
