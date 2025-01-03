import functools
import inspect
import typing

import pydantic
from tornado import web

from pydantictornado import errors

__all__ = [
    'Body',
    'ExplicitOpenAPIDocumentation',
    'Marker',
    'OpenAPIMethodInfo',
    'expose_operation',
]


class Marker:
    """Base class for all annotations."""


class Body(Marker):
    """Annotate a parameter as a request body."""

    def __init__(
        self,
        *,
        description: str | None = None,
        required: bool = False,
    ) -> None:
        self.description = description
        self.required = required


ModelType = typing.TypeVar('ModelType', bound=pydantic.BaseModel)
RequestMethod = typing.Callable[..., typing.Awaitable[None] | None]


class ExplicitOpenAPIDocumentation(typing.TypedDict, total=False):
    """Typed arguments for api.decorate."""

    default_status: typing.NotRequired[int]
    operation_id: typing.NotRequired[str]
    summary: typing.NotRequired[str]
    tags: typing.NotRequired[list[str]]


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
    """Expose a request handling method."""
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
                    raise web.HTTPError(
                        422,
                        'failed to validate request body: %s',
                        exc.errors(),
                    ) from exc
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
    model: typing.NotRequired[type[pydantic.BaseModel] | None]
    description: typing.NotRequired[str | None]


def add_error_response(
    status_code: int,
    **kwargs: typing.Unpack[ErrorResponseDefinition],
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[ModelType | None]]],
    typing.Callable[..., typing.Awaitable[ModelType | None]],
]:
    def wrapper(
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> typing.Callable[..., typing.Awaitable[ModelType | None]]:
        try:
            marker = OpenAPIMethodInfo.extract(func)
        except errors.MarkerNotFoundError:
            marker = OpenAPIMethodInfo()
            marker.attach(func)
        marker.errors[status_code] = kwargs
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
    def extract(cls, obj: object) -> 'OpenAPIMethodInfo':
        """Extract the OpenAPIMethodInfo instance from request handler."""
        unspecified = object()
        marker = getattr(obj, '__pydantic_tornado_method__', unspecified)
        if marker is unspecified:
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


class StructuredError[T: pydantic.BaseModel](web.HTTPError):
    body: T

    def __init__(
        self,
        status_code: int,
        body: T,
        log_message: str | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        if body is None:
            raise TypeError('body must be a pydantic model instance')
        super().__init__(status_code, log_message, *args, **kwargs)
        self.body = body
