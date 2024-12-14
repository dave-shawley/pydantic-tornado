import re

from tornado import routing


class Marker:
    pass


class Body(Marker):
    def __init__(self, description: str | None = None) -> None:
        self.description = description


def snake_case_operation_name(http_method: str, rule: routing.Rule) -> str:
    cls_name = re.sub(r'(?<!^)(?=[A-Z])', '_', rule.target.__name__).lower()
    return f'{cls_name}_{http_method.lower()}'
