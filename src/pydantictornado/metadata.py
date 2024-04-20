import types
import typing

from pydantictornado import util

T = typing.TypeVar('T')
Metadatum: typing.TypeAlias = object
ExactMetadatum = typing.TypeVar('ExactMetadatum')

ATTRIBUTE_NAME = '__MetadataMixin_pydantictornado_metadata'
"""Name of attribute used to add metadata to non-types

[typing.Annotated][] is only applicable to types. We occasionally
want to attach metadata to other things (eg, methods, functions).
In these cases, we attach a tuple of annotation values using this
attribute name.
"""


class MetadataMixin:
    def __init__(self) -> None:
        super().__init__()
        self.__pydantictornado_metadata = ()

    @property
    def metadata(self) -> typing.Sequence[Metadatum]:
        return self.__pydantictornado_metadata


def append(obj: T, *md: Metadatum) -> T:
    if isinstance(obj, types.FunctionType):
        obj = typing.cast(T, util.clone_function(obj))
    mds = list(getattr(obj, ATTRIBUTE_NAME, ()))
    mds.extend(md)
    setattr(obj, ATTRIBUTE_NAME, tuple(mds))
    return obj


def append_from(
    obj: object,
    others: typing.Iterable[object],
) -> None:
    mds = list(getattr(obj, ATTRIBUTE_NAME, ()))
    for other in others:
        mds.extend(getattr(other, ATTRIBUTE_NAME, ()))
    setattr(obj, ATTRIBUTE_NAME, tuple(mds))


def collect(
    obj: object, md_type: type[ExactMetadatum]
) -> typing.Sequence[ExactMetadatum]:
    return [
        md
        for md in getattr(obj, ATTRIBUTE_NAME, ())
        if isinstance(md, md_type)
    ]


def extract(obj: object) -> typing.Iterable[object]:
    yield from getattr(obj, ATTRIBUTE_NAME, ())
