class PydanticTornadoError(Exception):
    """Root of all errors raised by this library"""


class TypeRequiredError(PydanticTornadoError, TypeError):
    """A non-type was given where a type was required

    If you are seeing this, then you probably used a [special form]
    where a [type] is required.

    [special form]: https://docs.python.org/3/library/typing.html#special-forms
    [type]: https://docs.python.org/3/library/typing.html#the-type-of-class-objects

    """

    def __init__(self, value: object) -> None:
        cls = type(value)
        super().__init__(
            f'Type required - {value!r} is a {cls.__module__}.{cls.__name__}'
        )
        self.value = value


class NotSerializableError(PydanticTornadoError, TypeError):
    """Value is not a serializable type"""

    def __init__(self, value: object) -> None:
        super().__init__(
            f'Object of type {value.__class__.__name__} '
            f'is not serializable'
        )
        self.value = value
