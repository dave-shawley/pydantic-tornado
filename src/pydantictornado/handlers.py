import functools
import inspect
import typing
from collections import abc

import pydantic
from tornado import routing, web

from pydantictornado import api, errors, models, openapi

ModelType = typing.TypeVar('ModelType', bound=pydantic.BaseModel)
RequestHandler = typing.TypeVar('RequestHandler', bound=web.RequestHandler)
RequestMethodWithBody = typing.Callable[
    [RequestHandler, ModelType], typing.Awaitable[None]
]


class OpenAPIApplication(web.Application):
    def __init__(
        self, handlers: abc.Sequence[routing.Rule], **settings: object
    ) -> None:
        self.openapi_doc = openapi.OpenAPIDocument()
        rules = list(handlers)
        for rule in rules:
            for name, value in inspect.getmembers(
                rule.target, inspect.iscoroutinefunction
            ):
                if (
                    issubclass(rule.target, web.RequestHandler)
                    and name.upper() in rule.target.SUPPORTED_METHODS
                ):
                    self.openapi_doc.add_operation(name.upper(), rule, value)
        super().__init__(rules, **settings)  # type: ignore[arg-type]


class OpenDocAPIHandler(web.RequestHandler):
    application: OpenAPIApplication

    def get(self) -> None:
        self.write(self.application.openapi_doc.render())


class ExplicitOpenAPIDocumentation(typing.TypedDict, total=False):
    default_status: typing.NotRequired[int]
    operation_id: typing.NotRequired[str]
    summary: typing.NotRequired[str]
    tags: typing.NotRequired[list[str]]


@typing.overload
def decorate(
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[pydantic.BaseModel | None]]],
    models.RequestMethod,
]: ...


@typing.overload
def decorate(
    some_args: str, /, **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation]
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[pydantic.BaseModel | None]]],
    models.RequestMethod,
]: ...


@typing.overload
def decorate(
    func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    /,
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> models.RequestMethod: ...


def decorate(  # noqa: C901
    *args: typing.Callable[..., typing.Awaitable[ModelType | None]] | str,
    **kwargs: typing.Unpack[ExplicitOpenAPIDocumentation],
) -> (
    models.RequestMethod
    | typing.Callable[
        [typing.Callable[..., typing.Awaitable[ModelType | None]]],
        models.RequestMethod,
    ]
):
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

    def outer(  # noqa: C901
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> models.RequestMethod:
        marker = models.OpenAPIMethodMarker(extra=kwargs)
        body_cls: type[pydantic.BaseModel] | None = None

        sig = inspect.signature(func)
        if sig.return_annotation is not inspect.Signature.empty:
            marker.response_type = sig.return_annotation
        for name, param in sig.parameters.items():
            param_type = param.annotation
            if typing.get_origin(param_type) is typing.Annotated:
                param_type, *rest = typing.get_args(param_type)
                for arg in rest:
                    match arg:
                        case api.Body | api.Body():
                            if not inspect.isclass(param_type):
                                raise errors.UnsupportedAnnotationError(
                                    type(param_type)
                                )
                            if not issubclass(param_type, pydantic.BaseModel):
                                raise errors.UnsupportedAnnotationError(
                                    type(param_type)
                                )
                            body_cls = param_type
                            marker.set_request_body(
                                param.name,
                                param_type,
                                arg if isinstance(arg, api.Body) else None,
                            )

            if marker.request_body:
                continue

            if name not in ('self', 'cls'):
                if not inspect.isclass(param_type):
                    raise errors.UnsupportedAnnotationError(type(param_type))
                if param_type is inspect.Signature.empty:
                    raise errors.UnsupportedAnnotationError()
                marker.parameters[name] = param

        @functools.wraps(func)
        async def wrapper(
            self: web.RequestHandler, *args: object, **kwargs: object
        ) -> None:
            if body_cls is not None:
                try:
                    body = body_cls.model_validate_json(self.request.body)
                except pydantic.ValidationError as exc:
                    raise web.HTTPError(
                        422,
                        'failed to validate request body: %s',
                        exc.errors(),
                    ) from exc
                maybe_response = await func(self, body, *args, **kwargs)
            else:
                maybe_response = await func(self, *args, **kwargs)

            if status_code := marker.extra.get('default_status'):
                self.set_status(typing.cast(int, status_code))
            if isinstance(maybe_response, pydantic.BaseModel):
                self.set_header('content-type', 'application/json')
                self.write(maybe_response.model_dump_json())

        marker.attach(wrapper)

        return wrapper

    if func_provided is not None:
        return outer(func_provided)

    return outer
