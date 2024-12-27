import functools
import importlib.resources
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

    def tag_operation(
        self, rule_name: str, method: str, *tags: str | models.Tag
    ) -> None:
        """Tag an operation in the OpenAPI document.

        You can pass the tag as a string if the tag already exists
        in `openapi_doc`. Otherwise, pass a `Tag` instance to create
        a new tag and apply it to the operation.

        :raises ValueError: if the rule name is not found

        """
        rule = self.find_rule_by_name(rule_name)
        self.openapi_doc.tag_operation(rule, method, *tags)

    def find_rule_by_name(self, rule_name: str) -> routing.Rule:
        """Find a named rule in the active routes.

        :raises ValueError: if the rule name is not found

        """

        def search(
            router: routing.ReversibleRuleRouter,
        ) -> routing.Rule | None:
            if rule_name in router.named_rules:
                return typing.cast(routing.Rule, router.named_rules[rule_name])

            for rule in router.rules:
                if isinstance(rule.target, routing.ReversibleRuleRouter):
                    match = search(rule.target)
                    if match is not None:
                        return match

            return None

        rule = search(self.default_router)
        if rule is None:
            raise errors.RuleNotFoundError(rule_name)
        return rule


class OpenAPISpecHandler(web.RequestHandler):
    application: OpenAPIApplication

    def get(self) -> None:
        self.write(self.application.openapi_doc.render())


class OpenAPIDocHandler(web.RequestHandler):
    application: OpenAPIApplication
    _html_content: typing.ClassVar[str] = ''
    _file_timestamp: typing.ClassVar[float] = 0.0

    def get(self) -> None:
        self.set_header('content-type', 'text/html')
        self.set_header('cache-control', 'public, max-age=3600')
        self.write(self.get_html_content())

    @classmethod
    def get_html_content(cls) -> str:
        if not cls._html_content:
            path = (
                importlib.resources.files('pydantictornado') / 'openapi.html'
            )
            cls._html_content = path.read_text(encoding='utf-8')
        return cls._html_content


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


def decorate(  # noqa: C901, PLR0915
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

    def outer(  # noqa: C901, PLR0912, PLR0915
        func: typing.Callable[..., typing.Awaitable[ModelType | None]],
    ) -> models.RequestMethod:
        marker = models.OpenAPIMethodMarker(extra=kwargs)
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
                            body_param = param
                            body_cls = param_type
                            marker.set_request_body(
                                param.name,
                                param_type,
                                arg if isinstance(arg, api.Body) else None,
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
        async def wrapper(
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

            remaining_args = list(args)
            for arg in positional_args:
                if arg is body_param:
                    converted_args.append(body)
                else:
                    converted_args.append(
                        convert_parameter_value(arg, remaining_args.pop(0))
                    )

            for name, item in kwargs.items():
                converted_kwargs[name] = convert_parameter_value(
                    keyword_args[name], item
                )

            maybe_response = await func(
                self, *converted_args, **converted_kwargs
            )

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


def convert_parameter_value(
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
