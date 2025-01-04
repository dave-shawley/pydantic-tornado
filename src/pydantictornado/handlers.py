import http.client
import importlib.resources
import inspect
import json
import types
import typing
from collections import abc

import pydantic
from tornado import httputil, routing, web

from pydantictornado import api, errors, models, openapi


class ErrorKwargs[E](typing.TypedDict, total=False):
    exc_info: typing.NotRequired[
        tuple[
            type[E],
            E,
            types.TracebackType | None,
        ]
    ]
    reason: typing.NotRequired[str]


class ResponseFormatter:
    @typing.overload
    def format_body(
        self, _request: httputil.HTTPServerRequest, /, body: None
    ) -> None: ...

    @typing.overload
    def format_body(
        self, _request: httputil.HTTPServerRequest, /, body: object
    ) -> tuple[str, bytes]: ...

    def format_body(
        self,
        _request: httputil.HTTPServerRequest,
        /,
        body: object | None,
    ) -> tuple[str, bytes] | None:
        if isinstance(body, pydantic.BaseModel):
            body = body.model_dump(by_alias=True, mode='json')
        if body is not None and not isinstance(body, str | bytes):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode('utf-8')
        return ('application/json', body) if body is not None else None

    def format_error(
        self,
        handler: web.RequestHandler,
        status_code: int,
        **kwargs: typing.Unpack[ErrorKwargs[BaseException]],
    ) -> tuple[str, bytes]:
        exc = exc_info[1] if (exc_info := kwargs.get('exc_info')) else None
        if isinstance(exc, api.StructuredError):
            return self.format_body(  # cast required for mypy :(
                handler.request, typing.cast(pydantic.BaseModel, exc.body)
            )

        reason = ''
        if isinstance(exc, web.HTTPError):
            reason = exc.reason
        if not reason:
            reason = kwargs.get(
                'reason', http.client.responses.get(status_code, 'Unknown')
            ).title()
        body = {'status': status_code, 'title': reason}
        if isinstance(exc, errors.BodyValidationError):
            formatted = exc.error.errors(
                include_url=False, include_input=False, include_context=False
            )
            if formatted:
                body['detail'] = formatted[0]['msg']
            else:
                body['detail'] = exc.error.title or str(exc.error)
            body['errors'] = formatted
        elif exc is not None:
            body['detail'] = str(exc)
        return self.format_body(handler.request, body)


class OpenAPIApplication(ResponseFormatter, web.Application):
    def __init__(
        self, handlers: list[routing.Rule], **settings: object
    ) -> None:
        self.openapi_doc = openapi.OpenAPIDocument()
        self._process_rules(handlers)
        super().__init__(handlers, **settings)  # type: ignore[arg-type]
        self.error_models: dict[int, type[pydantic.BaseModel]] = {}

    def add_handlers(
        self, host_pattern: str, host_handlers: list[typing.Any]
    ) -> None:
        self._process_rules(host_handlers)
        super().add_handlers(host_pattern, host_handlers)

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

    def register_error_model(
        self,
        status_code: int,
        error_model: type[pydantic.BaseModel],
        *,
        description: str | None = None,
    ) -> None:
        """Register a model as the default response for a HTTP status code."""
        self.error_models[status_code] = error_model
        self.openapi_doc.set_default_error_model(
            status_code, error_model, description=description
        )

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

    def _process_rules(self, rules: abc.Sequence[object]) -> None:
        for rule in (r for r in rules if isinstance(r, routing.Rule)):
            for name, value in inspect.getmembers(
                rule.target, inspect.iscoroutinefunction
            ):
                if (
                    issubclass(rule.target, web.RequestHandler)
                    and name.upper() in rule.target.SUPPORTED_METHODS
                ):
                    self.openapi_doc.add_operation(name.upper(), rule, value)


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


class PydanticErrorHandler(web.RequestHandler):
    application: OpenAPIApplication

    def __init__(
        self,
        application: web.Application,
        request: httputil.HTTPServerRequest,
        **kwargs: object,
    ) -> None:
        self.__default_status = 200
        super().__init__(application, request, **kwargs)

    def set_default_status(self, status_code: int) -> None:
        self.__default_status - status_code

    def clear(self) -> None:
        super().clear()
        self.set_status(self.__default_status)
        self.clear_header('content-type')

    def write_error(  # type: ignore[override]
        self,
        status_code: int,
        **kwargs: typing.Unpack[ErrorKwargs[BaseException]],
    ) -> None:
        content_type, body = self.application.format_error(
            self, status_code, **kwargs
        )
        self.set_header('content-type', content_type)
        self.write(body)
        self.finish()
