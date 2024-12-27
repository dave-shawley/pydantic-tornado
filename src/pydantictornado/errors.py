class Error(Exception):
    pass


class DuplicateTagError(Error, ValueError):
    """Tag already exists in the OpenAPI document."""

    def __init__(self, tag_name: str) -> None:
        super().__init__(f'Tag {tag_name} already exists')


class MarkerNotFoundError(Error):
    pass


class OperationNotFoundError(Error, ValueError):
    """Operation not found in the OpenAPI document."""

    def __init__(self, path: str, http_method: str) -> None:
        super().__init__(f'Operation not found for {http_method} {path}')
        self.http_method = http_method
        self.path_expression = path


class RuleNotFoundError(Error, ValueError):
    """Rule not found in the active routes."""

    def __init__(self, rule_name: str) -> None:
        super().__init__(f'Rule {rule_name} not found')
        self.rule_name = rule_name


class TagNotFoundError(Error, ValueError):
    """Tag not found in the OpenAPI document."""

    def __init__(self, tag_name: str) -> None:
        super().__init__(f'Tag {tag_name} not found')


class UnsupportedAnnotationError(Error):
    """Annotation is not supported by the OpenAPI decorators."""


class UnsupportedParameterError(Error):
    """Parameter is not supported by the OpenAPI decorators."""

    def __init__(self, param_name: str, reason: str) -> None:
        super().__init__(f'Parameter {param_name} is not supported: {reason}')
        self.param_name = param_name
        self.reason = reason
