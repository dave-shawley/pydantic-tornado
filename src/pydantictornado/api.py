import functools
import inspect
import typing

import pydantic
from tornado import web

from pydantictornado import errors


class Marker:
    """Base class for all annotations."""


class RequestBodyDescription(typing.TypedDict):
    """Keyword arguments for [pydantictornado.api.Body][]."""

    description: typing.NotRequired[str | None]
    """Description of the request body."""

    required: typing.NotRequired[bool]
    """Is the request body required?"""


class Body(Marker):
    """Annotate a parameter as a request body."""

    __defaults: typing.Final[RequestBodyDescription] = {
        'description': None,
        'required': False,
    }

    def __init__(
        self,
        **kwargs: typing.Unpack[RequestBodyDescription],
    ) -> None:
        self.openapi = self.__defaults | kwargs


ModelType = typing.TypeVar('ModelType', bound=pydantic.BaseModel)
RequestMethod = typing.Callable[..., typing.Awaitable[None] | None]


class ExplicitOpenAPIDocumentation(typing.TypedDict, total=False):
    """Keyword arguments for [pydantictornado.api.expose_operation][].

    This dictionary describes the optional keyword parameters to the
    [api.expose_operation][pydantictornado.api.expose_operation] decorator.
    They are used to populate the OpenAPI specification of the operation
    with additional information.

    """

    default_status: typing.NotRequired[int]
    """The default status code is 200. This property allows you to override
    the value that is used if your handler does not set an explicit status
    code. This is commonly used for `DELETE` and `POST` operations where
    you would use "204 No Content" or "303 See Other" instead of a 200.
    """

    operation_id: typing.NotRequired[str]
    """The default operation ID is generated from the request handler class
    name and HTTP method name. Use this keyword parameter to set an explicit
    operation ID.
    """

    summary: typing.NotRequired[str]
    """The default summary is the first line of the method's docstring or
    the operation ID if no docstring is present. Use this keyword parameter
    to explicitly set the summary.
    """

    description: typing.NotRequired[str]
    """The default description is the remainder of the docstring after the
    first line. Use this keyword parameter to explicitly set the
    description."""

    tags: typing.NotRequired[list[str]]
    """Set tags for the operation."""


@typing.overload
def expose_operation(
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[pydantic.BaseModel | None]]],
    RequestMethod,
]: ...


@typing.overload
def expose_operation(
    some_args: str, /, **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation]
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[pydantic.BaseModel | None]]],
    RequestMethod,
]: ...


@typing.overload
def expose_operation(
    func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    /,
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> RequestMethod: ...


def expose_operation(  # noqa: C901, PLR0915
    *args: typing.Callable[..., typing.Awaitable[ModelType | None]] | str,
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> (
    RequestMethod
    | typing.Callable[
        [typing.Callable[..., typing.Awaitable[ModelType | None]]],
        RequestMethod,
    ]
):
    """
    Exposes an operation to be used as an endpoint in an HTTP framework.

    This decorator is used to expose a function as an HTTP endpoint in the
    OpenAPI specification. The decorated method must be a coroutine that
    receives the deserialized request body as a parameter and returns the
    response as a Pydantic model instance. The decorator returns a wrapper
    function that uses the standard Tornado RequestHandler signature.

    When the wrapper method is invoked, it converts `request.body` to a
    Pydantic model instance and passes it to the decorated method. Path
    parameters are passed as positional arguments after being coerced to
    the appropriate type. The return value from the method is serialized
    as JSON and written to the response.

    ```python
    @api.expose_operation
    async def post(
        self, item_id: int, *,
        body: typing.Annotated[CreataRequest, api.Body]
    ) -> Item: ...
    ```

    The decorator takes care of the following tasks for you:

    1. Extracts the request body from the request and converts it to a
       `CreateRequest` instance. The body parameter is identified by
       adding an [api.Body][pydantictornado.api.Body] annotation.
    2. Converts the `item_id` path parameter from a string to an integer
    3. Invokes the `post` method with the converted arguments
    4. Converts the `Item` return value to a JSON string and writes it as
       the response
    5. Captures Pydantic errors and returns an appropriate response

    In addition to transforming the request and response, the operation is
    added to the OpenAPI specification based on how it is registered in the
    application router. Additional OpenAPI properties can be supplied as
    keyword arguments to the decorator. See [ExplicitOpenAPIDocumentation]
    [pydantictornado.api.ExplicitOpenAPIDocumentation] for a list of supported
    properties.

    Raises:
        errors.BodyValidationError: Occurs during request validation if the
            body does not match the Pydantic model attribute's validation
            rules.
        errors.MarkerNotFoundError: Occurs when the OpenAPI marker is not found
            on the provided function but additional OpenAPI metadata needs to
            be attached.
        errors.ParameterUsageError: Raised when multiple body parameters are
            detected.
        TypeError: Raised when the provided arguments do not conform to the
            expected types (e.g., non-callable or non-coroutine function passed
            as the first argument).
        errors.UnsupportedAnnotationError: Raised when an unsupported type is
            provided for a parameter annotation, such as missing type
            annotations or an unsupported special form.
    """
    func_provided: (
        typing.Callable[..., typing.Awaitable[ModelType | None]] | None
    ) = None
    if args:
        if len(args) != 1:
            raise TypeError(
                f'only one positional argument is allowed, got {len(args)}'
            )
        if not callable(args[0]):
            raise TypeError(
                f'expected a callable, got {type(args[0])} instead'
            )
        if not inspect.iscoroutinefunction(args[0]):
            raise TypeError(
                f'expected a coroutine function, got {type(args[0])} instead'
            )
        func_provided = typing.cast(
            typing.Callable[..., typing.Awaitable[ModelType | None]],
            args[0],
        )

    def outer(  # noqa: C901, PLR0912, PLR0915
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> RequestMethod:
        try:
            marker = OpenAPIMethodInfo.extract(func)
        except errors.MarkerNotFoundError:
            marker = OpenAPIMethodInfo()

        if docstring := inspect.getdoc(func):
            parts = docstring.splitlines()
            kwargs.setdefault('summary', parts[0])
            if len(parts) > 1:
                kwargs.setdefault(
                    'description',
                    '\n'.join(parts[1 if parts[1].strip() else 2 :]),
                )

        marker.extra.update(kwargs)
        body_cls: type[pydantic.BaseModel] | None = None
        body_param: inspect.Parameter | None = None

        positional_args: list[inspect.Parameter] = []
        keyword_args: dict[str, inspect.Parameter] = {}

        sig = inspect.signature(func)
        if sig.return_annotation is not inspect.Signature.empty:
            marker.response_type = sig.return_annotation

        all_params = list(sig.parameters.items())
        for name, param in all_params[1:]:  # skip the `self` parameter
            param_type = param.annotation
            if typing.get_origin(param_type) is typing.Annotated:
                param_type, *rest = typing.get_args(param_type)
                for arg in rest:
                    if arg is Body or isinstance(arg, Body):
                        if not inspect.isclass(param_type):
                            raise errors.UnsupportedAnnotationError(
                                type(param_type)
                            )
                        if not issubclass(param_type, pydantic.BaseModel):
                            raise errors.UnsupportedAnnotationError(
                                type(param_type)
                            )
                        if body_param is not None:
                            raise errors.ParameterUsageError(
                                'More than one body parameter is not allowed'
                            )
                        body_param = param
                        body_cls = param_type
                        marker.set_request_body(
                            param.name,
                            param_type,
                            arg if isinstance(arg, Body) else None,
                        )

            if not inspect.isclass(param_type):
                raise errors.UnsupportedAnnotationError(type(param_type))
            if param_type is inspect.Signature.empty:
                raise errors.UnsupportedAnnotationError()

            if issubclass(param_type, Body) and issubclass(
                param_type, pydantic.BaseModel
            ):
                if body_param is not None:
                    raise errors.ParameterUsageError(
                        'More than one body parameter is not allowed'
                    )
                body_param = param
                body_cls = param_type
                marker.set_request_body(param.name, param_type, None)

            if param is not body_param:
                marker.parameters[name] = param

            if param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                positional_args.append(param)
            elif param.kind == inspect.Parameter.KEYWORD_ONLY:
                keyword_args[name] = param
            else:
                raise errors.UnsupportedParameterError(
                    name, f'kind {param.kind} is not supported'
                )

        # At this point, positional_args is an ordered list of
        # non-keyword parameters. If the body parameter was found,
        # and it is not a keyword-only parameter, then, the
        # Parameter instance will be in positional_args. We use
        # identity comparison when creating the parameters used
        # to call the method.
        #
        # keyword_args contains the keyword only parameters.

        @functools.wraps(func)
        async def wrapper(  # noqa: C901
            self: web.RequestHandler, *args: str, **kwargs: str
        ) -> None:
            # Method is invoked as `op(*converted_args, **converted_kwargs)`
            # where converted_args is the ordered sequence of non-keyword-only
            # parameters
            converted_args: list[object] = []
            converted_kwargs: dict[str, object] = {}

            body = None
            if body_cls is not None:
                if body_param is None:  # pragma: no cover
                    raise RuntimeError('body_cls is set but body_param is not')
                try:
                    body = body_cls.model_validate_json(self.request.body)
                except pydantic.ValidationError as exc:
                    raise errors.BodyValidationError(exc) from None
                if body_param.kind == inspect.Parameter.KEYWORD_ONLY:
                    converted_kwargs[body_param.name] = body

            if default_status := marker.extra.get('default_status'):
                if hasattr(self, 'set_default_status'):
                    self.set_default_status(typing.cast(int, default_status))
                self.set_status(typing.cast(int, default_status))

            remaining_args = list(args)
            for arg in positional_args:
                if arg is body_param:
                    converted_args.append(body)
                elif arg.name in kwargs:
                    converted_args.append(
                        _convert_parameter_value(arg, kwargs.pop(arg.name))
                    )
                else:
                    converted_args.append(
                        _convert_parameter_value(arg, remaining_args.pop(0))
                    )

            for name, item in kwargs.items():
                converted_kwargs[name] = _convert_parameter_value(
                    keyword_args[name], item
                )

            maybe_response = await func(
                self, *converted_args, **converted_kwargs
            )

            if isinstance(maybe_response, pydantic.BaseModel):
                self.set_header('content-type', 'application/json')
                self.write(maybe_response.model_dump_json())

        marker.attach(wrapper)

        return wrapper

    if func_provided is not None:
        return outer(func_provided)

    return outer


class ErrorResponseDefinition(typing.TypedDict):
    """Keyword parameters to [pydantictornado.api.add_error_response][]."""

    model: typing.NotRequired[type[pydantic.BaseModel] | None]
    """Model that describes the error response body This parameter
    overrides the model registered by calling the `register_error_model`
    method on the application instance."""

    description: typing.NotRequired[str | None]
    """Description of this error response."""


def add_error_response(
    status_code: int,
    **kwargs: typing.Unpack[ErrorResponseDefinition],
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[ModelType | None]]],
    typing.Callable[..., typing.Awaitable[ModelType | None]],
]:
    """Document an error returned from an operation.

    Stack this decorator with
    [api.expose_operation][pydantictornado.api.expose_operation] to document
    that an operation returns a specific status code. THe status code and
    other parameters are added to the OpenAPI responses section for the
    decorated operation.

    This decorator works in conjunction with the [register_error_model]
    [pydantictornado.handlers.OpenAPIApplication.register_error_model]
    method of the application instance. You *should* bind the error model
    and status code in the application instance when the same model is
    used across many handlers. Including the `model` parameter in this
    decorator is optional and will override any model registered in the
    application instance.

    Args:
        status_code: The HTTP status code of the error response.
        model: The Pydantic model that describes the error response body.
        description: A description of the error response.

    """

    def wrapper(
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> typing.Callable[..., typing.Awaitable[ModelType | None]]:
        marker = OpenAPIMethodInfo.extract(func, create_if_missing=True)
        marker.errors[status_code] = kwargs
        return func

    return wrapper


class ResponseHeaderDefinition(typing.TypedDict):
    """Keyword parameters to [pydantictornado.api.add_response_header][]."""

    description: typing.NotRequired[str | None]
    """Description of the header."""

    model: typing.NotRequired[type[pydantic.BaseModel] | None]
    """Model that describes the header value."""

    required: typing.NotRequired[bool]
    """Is the header *always* returned?"""

    explode: typing.NotRequired[bool]
    """Are multiple values be represented as separate parameters?

    Note that the OpenAPI `style` property is *awlays* `simple` for
    headers. The [style examples in OpenAPI 3.1](
    https://spec.openapis.org/oas/v3.1.1.html#style-examples)
    describe the result of using this parameter.
    """

    deprecated: typing.NotRequired[bool]
    """Is this header deprecated?"""

    for_status: typing.NotRequired[list[int] | int]
    """Status codes for which this header is returned. If this parameter
    is omitted, then the header is included for all status codes."""


def add_response_header(
    name: str, **kwargs: typing.Unpack[ResponseHeaderDefinition]
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[ModelType | None]]],
    typing.Callable[..., typing.Awaitable[ModelType | None]],
]:
    """Describe a response header returned from a decorated operation.

    This decorator adds a response header with the provided definition with
    a decorated operation. Most of the parameters are copied as-is into the
    OpenAPI specification. The `model` parameter should be included for complex
    header definitions that have a specific structure.

    The `for_status` parameter can be used to limit the header to specific
    response status codes. If omitted, the header is included for all status
    codes.

    Args:
        name: The name of the response header
        description: A description of the header.
        model: The Pydantic model that describes the header value.
        required: Is the header *always* returned?
        explode: Are multiple values be represented as separate parameters?
        deprecated: Is this header deprecated?
        for_status: Status codes for which this header is returned.

    """

    def wrapper(
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> typing.Callable[..., typing.Awaitable[ModelType | None]]:
        marker = OpenAPIMethodInfo.extract(func, create_if_missing=True)
        marker.headers[name] = kwargs
        return func

    return wrapper


def _convert_parameter_value(
    param_def: inspect.Parameter, value: str
) -> str | int | bool | float | None:
    try:
        if param_def.annotation is int:
            return int(value)
        if param_def.annotation is bool:
            return value.lower() in ('true', '1')
        if param_def.annotation is float:
            return float(value)
    except (TypeError, ValueError):
        raise web.HTTPError(400) from None
    return value


class _BodyParameterInfo:
    def __init__(
        self,
        *,
        name: str,
        type_: type[pydantic.BaseModel],
        metadata: Body | None,
    ) -> None:
        self.name = name
        self.type = type_
        self.metadata = Body() if metadata is None else metadata


class OpenAPIMethodInfo:
    """Attached to a method to provide OpenAPI documentation.

    An instance of this class is attached to a method when it is
    decorated with `api.decorate`. It contains much of the content
    that is included in the generated OpenAPI documentation for the
    operation.

    The `extract` class method is used to identify exposed methods
    by the inner workings of the library.

    """

    EMPTY: 'OpenAPIMethodInfo'

    def __init__(
        self, *, response_type: type[pydantic.BaseModel] | None = None
    ) -> None:
        super().__init__()
        self._body_parameter_info: _BodyParameterInfo | None = None
        self.response_type = response_type
        self.parameters: dict[str, inspect.Parameter] = {}
        self.errors: dict[int, ErrorResponseDefinition] = {}
        self.headers: dict[str, ResponseHeaderDefinition] = {}
        self.extra: dict[str, object | str | int | bool | None | list[str]]
        self.extra = {}

    @property
    def request_body(self) -> _BodyParameterInfo | None:
        return self._body_parameter_info

    def set_request_body(
        self,
        name: str,
        type_: type[pydantic.BaseModel],
        metadata: Body | None,
    ) -> None:
        self._body_parameter_info = _BodyParameterInfo(
            name=name,
            type_=type_,
            metadata=metadata if metadata else Body(),
        )

    @classmethod
    def extract(
        cls, obj: object, *, create_if_missing: bool = False
    ) -> 'OpenAPIMethodInfo':
        """Extract the OpenAPIMethodInfo instance from request handler."""
        unspecified = object()
        marker = getattr(obj, '__pydantic_tornado_method__', unspecified)
        if marker is unspecified:
            if create_if_missing:
                marker = cls()
                marker.attach(obj)
                return marker
            raise errors.MarkerNotFoundError()
        if not isinstance(marker, OpenAPIMethodInfo):
            raise TypeError(
                f'expected OpenAPIMethodMarker, got {type(marker)}'
            )
        return marker if not marker.is_empty() else cls.EMPTY

    def attach(self, obj: object) -> None:
        setattr(obj, '__pydantic_tornado_method__', self)  # noqa: B010

    def is_empty(self) -> bool:
        """Does this instance contain information?"""
        return (
            not bool(self.parameters)
            and not bool(self.errors)
            and not bool(self.extra)
            and not bool(self.headers)
            and self._body_parameter_info is None
        )


class _FrozenMethodInfo(OpenAPIMethodInfo):
    def __init__(self) -> None:
        for attr in (
            '_body_parameter_info',
            'errors',
            'extra',
            'parameters',
            'response_type',
        ):
            object.__setattr__(self, attr, None)

    def __setattr__(self, _key: object, _value: object) -> None:
        raise TypeError('frozen method marker is immutable')


OpenAPIMethodInfo.EMPTY = _FrozenMethodInfo()


class WrapErrorParams(typing.TypedDict, total=False):
    reason: typing.NotRequired[str]
    """Optional HTTP reason phrase for the error response."""


class StructuredError(web.HTTPError):
    """An exception that includes a structured response body.

    This class exists because the [pydantic.BaseModel][] class is not
    compatible with the [Exception][] class. It would be nice to raise
    a model instance directly, but that is not possible. Instead, raise
    an instance of this class with the response body as the `body` attribute.

    """

    body: pydantic.BaseModel

    def __init__(
        self,
        status_code: int,
        body: pydantic.BaseModel,
        log_message: str | None = None,
        *args: object,
        **kwargs: typing.Unpack[WrapErrorParams],
    ) -> None:
        if body is None:
            raise TypeError('body must be a pydantic model instance')
        super().__init__(status_code, log_message, *args, **kwargs)
        self.body = body


def wrap_error(
    status_code: int,
    body: pydantic.BaseModel,
    log_message: str | None = None,
    *args: object,
    **kwargs: typing.Unpack[WrapErrorParams],
) -> web.HTTPError:
    """Wrap a pydantic model in an exception that can be raised.

    [pydantic.BaseModel][] instances cannot be raised directly as exceptions
    which makes them difficult to use for structured error responses. This
    function wraps the model instance in an exception that can be raised.
    Call this function from within a [handlers.PydanticErrorHandler]
    [pydantictornado.handlers.PydanticErrorHandler] instance to serialize
    `body` as the response body when an error is caught.

    ```python
    import pydantic
    from pydantictornado import api, handlers
    from tornado import web

    class NotFoundError(pydantic.BaseModel):
        item_id: str

        @pydantic.computed_field
        @property
        def message(self) -> str:
            return f'Item {self.item_id} not found'

    class MyHandler(handlers.PydanticErrorHandler, web.RequestHandler):
        @api.expose_operation
        @api.add_error_response(404, model=NotFoundError)
        async def get(self, item_id: str) -> None:
            raise api.wrap_error(404, NotFoundError(item_id=item_id))
    ```

    """
    return StructuredError(status_code, body, log_message, *args, **kwargs)
