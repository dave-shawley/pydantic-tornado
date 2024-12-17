import re
import secrets
import string
import types
import typing
import warnings

import pydantic
from tornado import routing

from pydantictornado import api, errors, models

if typing.TYPE_CHECKING:
    from collections import abc

_PARAM_PATTERN = re.compile(r'\((?P<part>[^()]*)\)')
_NAME_PATTERN = re.compile(r'<(?P<name>[^>]+)>')
_TINY_ID_CHARS = string.ascii_lowercase + string.digits


class ParsedUrlPath:
    def __init__(self) -> None:
        self.path = '/'
        self.positional_params: list[str] = []
        self.named_params: list[str] = []

    def set_path(self, path: str) -> None:
        self.path = '/' if path == '/?' else path.removesuffix('/?')

    def add_positional_param(self, name: str) -> None:
        self.positional_params.append(name)

    def add_named_param(self, name: str) -> None:
        self.named_params.append(name)


class OpenAPIDocument:
    def __init__(self) -> None:
        self.openapi_doc = models.OpenAPI()
        self.__model_map: dict[type[pydantic.BaseModel], models.Reference] = {}

    def render(self) -> dict[str, object]:
        return self.openapi_doc.model_dump(by_alias=True)

    def add_operation(
        self,
        http_method: str,
        rule: routing.Rule,
        func: models.RequestMethod,
    ) -> None:
        try:
            marker = models.OpenAPIMethodMarker.extract(func)
        except errors.MarkerNotFoundError:
            return

        if not isinstance(rule, routing.URLSpec):
            warnings.warn(
                f'{rule.__class__.__name__} rules are not supported',
                UserWarning,
                stacklevel=2,
            )
            return

        operation_attrs: abc.MutableMapping[str, typing.Any]
        operation_attrs = {}
        if marker.extra:
            operation_attrs.update(marker.extra)
        operation_attrs.setdefault(
            'operation_id', api.snake_case_operation_name(http_method, rule)
        )
        operation = models.Operation(**operation_attrs)

        if marker.body_param_type is not None:
            ref = self._add_model(marker.body_param_type)
            operation.request_body = models.RequestBody(
                description=marker.extra.get('description'),
                content={'application/json': models.Content(schema=ref)},
                required=True,
            )
        if marker.response_type is not None:
            ref = self._add_model(marker.response_type)
            operation.responses['200'] = models.Response(
                description='OK',
                content={'application/json': models.Content(schema=ref)},
            )

        working = rule.regex.pattern.removesuffix('$')
        parsed = _generate_openapi_path(working)
        try:
            path_item = self.openapi_doc.paths[parsed.path]
        except KeyError:
            path_item = models.PathItem()
            for value in marker.parameters.values():
                schema = _generate_schema(value.annotation)
                param = models.Parameter.model_validate(
                    {
                        'name': value.name,
                        'in': 'path',
                        'schema': schema,
                    }
                )
                if value.name not in parsed.named_params:
                    param.name = parsed.positional_params.pop(0)
                path_item.parameters.append(param)
            self.openapi_doc.paths[parsed.path] = path_item
        setattr(path_item, http_method.lower(), operation)

    def _add_model(self, model: type[pydantic.BaseModel]) -> models.Reference:
        try:
            ref = self.__model_map[model]
        except KeyError:
            pydantic_schema = model.model_json_schema(
                ref_template='#/components/schemas/{model}',
            )
            defs = pydantic_schema.pop('$defs', {})
            for name, value in defs.items():
                self.openapi_doc.components.schemas[name] = (
                    models.Schema.model_validate(value)
                )

            schema = models.Schema.model_validate(pydantic_schema)
            self.openapi_doc.components.schemas[model.__name__] = schema
            ref = models.Reference(
                ref=f'#/components/schemas/{model.__name__}',
            )
            self.__model_map[model] = ref

        return ref


def _generate_tiny_id(length: int = 8) -> str:
    return ''.join(secrets.choice(_TINY_ID_CHARS) for _ in range(length))


def _generate_openapi_path(path_pattern: str) -> ParsedUrlPath:
    parsed = ParsedUrlPath()
    path_pattern = path_pattern.removesuffix('$')
    while match := _PARAM_PATTERN.search(path_pattern):
        start, end = match.span()
        if match['part'].startswith('?'):
            qualifier, value = match['part'][:2], match['part'][2:]
            match qualifier:
                case '?P':
                    if name_match := _NAME_PATTERN.search(value):
                        value = f'{{{name_match["name"]}}}'
                        parsed.add_named_param(name_match['name'])
                    elif value.startswith('='):
                        name = value[1:]
                        value = f'{{{name}}}'
                        if name not in parsed.named_params:
                            raise RuntimeError('Invalid regular expression')
                    else:
                        raise RuntimeError('Invalid regular expression')
                case '?#':
                    value = ''
                case '?:':
                    pass  # value good to go
                case _:
                    raise RuntimeError(f'Unsupported qualifier: {qualifier}')
        else:
            name = _generate_tiny_id()
            parsed.add_positional_param(name)
            value = f'{{{name}}}'

        path_pattern = path_pattern[:start] + value + path_pattern[end:]

    parsed.set_path(path_pattern)

    return parsed


def _generate_schema(anno: type | None) -> models.Schema:
    if anno is types.NoneType or anno is None:
        return models.Schema(type='null')
    if issubclass(anno, pydantic.BaseModel):
        return models.Schema.model_validate(anno.model_json_schema())
    if issubclass(anno, bool):
        return models.Schema(type='boolean')
    if issubclass(anno, int):
        return models.Schema(type='number', format='int')
    if issubclass(anno, str):
        return models.Schema(type='string')
    raise RuntimeError(f'Unsupported type: {anno}')
