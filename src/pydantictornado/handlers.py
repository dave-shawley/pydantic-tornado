import importlib.resources
import inspect
import typing
from collections import abc

from tornado import routing, web

from pydantictornado import errors, models, openapi


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
