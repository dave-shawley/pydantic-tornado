import importlib.resources
import inspect
import typing
from collections import abc

import pydantic
from tornado import routing, web

from pydantictornado import errors, models, openapi


class OpenAPIApplication(web.Application):
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
