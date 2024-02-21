import collections.abc
import types
import typing
import warnings

from pydantictornado import util


def _initialize_primitive_map(
    m: collections.abc.MutableMapping[type, dict[str, typing.Any]]
) -> None:
    m.update(
        {
            bool: {'type': 'boolean'},
            int: {'type': 'integer'},
            float: {'type': 'number'},
            str: {'type': 'string'},
            types.NoneType: {'type': 'null'},
        }
    )


_primitive_map = util.ClassMapping[dict[str, typing.Any]](
    initialize_data=_initialize_primitive_map
)

# NB ... the type of typing.Literal[...] or typing.LiteralString
# cannot be included in a type annotation; however, they are
# matched as "object" instances.
Describable = types.GenericAlias | types.UnionType | type | object
Description = dict[str, typing.Any]


def describe_type(t: Describable) -> Description:
    """Describe `t` as an OpenAPI schema"""
    alias_args = None
    if isinstance(t, types.GenericAlias):
        alias_args, t = typing.get_args(t), typing.get_origin(t)
    if isinstance(t, types.UnionType):
        return {
            'anyOf': [
                describe_type(union_member)
                for union_member in typing.get_args(t)
            ]
        }

    if description := _describe_literals(t):
        return description

    if not isinstance(t, type):
        raise TypeError(f'Unexpected value of type {type(t)}')  # noqa: TRY003

    unspecified = {'': object()}
    if (v := _primitive_map.get(t, unspecified)) is not unspecified:
        return v.copy()

    if issubclass(t, collections.abc.Mapping):
        raise NotImplementedError('Mapping not implemented')

    if issubclass(t, collections.abc.Collection):
        return _describe_collection(t, alias_args)

    raise ValueError


def _describe_literals(t: Describable) -> Description | None:
    if t is None:
        return {'type': 'null'}
    if t is typing.LiteralString:
        return {'type': 'string'}
    if typing.get_origin(t) is typing.Literal:
        options = []
        for arg in typing.get_args(t):
            options.append(describe_type(type(arg)))
            options[-1]['const'] = arg
        if len(options) > 1:
            return {'anyOf': options}
        return options[0]
    return None


def _describe_collection(
    t: type,
    alias_args: tuple[Describable, ...] | None,
) -> Description:
    description: Description = {'type': 'array'}
    if alias_args:
        if issubclass(t, tuple):
            description['prefixItems'] = [
                describe_type(item) for item in alias_args
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
            description['items'] = describe_type(alias_args[0])
    return description
