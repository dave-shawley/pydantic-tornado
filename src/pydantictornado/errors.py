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


class ConfigurationError(PydanticTornadoError):
    """Root of initialization errors

    This category of errors occurs during route creation.

    """


class UnroutableParameterTypeError(PydanticTornadoError, TypeError):
    """Unhandled type annotation on a request method"""

    def __init__(self, cls: type) -> None:
        super().__init__(
            f'Type {cls.__name__} is not recognized by {self.__module__}'
        )


class CoroutineRequiredError(ConfigurationError, ValueError):
    def __init__(self, value: object) -> None:
        value_description = getattr(value, '__name__', value)
        super().__init__(f'{value_description!r} is not a coroutine')


class NoHttpMethodsDefinedError(ConfigurationError):
    """Route does not have a handler defined"""

    def __init__(self) -> None:
        super().__init__('At least one HTTP method implementation is required')


class ValueParseError(PydanticTornadoError, ValueError):
    """Failed to parse a request parameter value according to its type"""

    def __init__(self, value: object, cls: type) -> None:
        super().__init__(f'failed to parse {value!r} as a {cls.__name__}')
        self.expected_type = cls
        self.value = value
