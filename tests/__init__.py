import typing

T = typing.TypeVar('T')


def assert_is_not_none(value: T | None, *, msg: str | None = None) -> T:
    """Help mypy understand that assertIsNotNone is fatal if value is None"""
    if value is None:
        raise AssertionError('unexpectedly None' if msg is None else msg)
    return value
