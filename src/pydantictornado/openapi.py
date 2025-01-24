import collections
import http.client
import logging
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
        self.logger = logging.getLogger(__package__).getChild(
            self.__class__.__name__
        )
        self.openapi_doc = models.OpenAPI()
        self.__model_map: dict[type[pydantic.BaseModel], models.Reference] = {}
        self.__marker_map: dict[str, api.OpenAPIMethodInfo] = {}
        self.__defaulted_errors: dict[
            int, list[tuple[models.Operation, api.ErrorResponseDefinition]]
        ]
        self.__defaulted_errors = collections.defaultdict(list)
        self.__default_error_models: dict[int, models.Reference] = {}
        self.__global_status_codes: set[int] = set()

    def render(self) -> dict[str, object]:
        return self.openapi_doc.model_dump(by_alias=True)

    def add_global_error(
        self,
        status_code: int,
        model: type[pydantic.BaseModel],
        *,
        description: str | None = None,
    ) -> None:
        self.__global_status_codes.add(status_code)
        model_ref = self.set_default_error_model(
            status_code, model, description=description
        )
        content = models.Content(schema=model_ref)
        description = description or http.client.responses.get(
            status_code, f'Unknown HTTP {status_code}'
        )

        for path in self.openapi_doc.paths.values():
            for field_name in path.model_fields_set:
                operation = getattr(path, field_name)
                if isinstance(operation, models.Operation):
                    operation.responses.setdefault(
                        str(status_code),
                        models.Response(
                            description=description,
                            content={'application/json': content},
                        ),
                    )

    def add_operation(  # noqa: C901, PLR0912, PLR0915
        self,
        http_method: str,
        rule: routing.URLSpec,
        func: api.RequestMethod,
    ) -> None:
        try:
            marker = api.OpenAPIMethodInfo.extract(func)
        except errors.MarkerNotFoundError:
            return

        operation_attrs: abc.MutableMapping[str, typing.Any]
        operation_attrs = {}
        if marker.extra:
            operation_attrs.update(marker.extra)
        operation_attrs.setdefault(
            'operation_id', _snake_case_operation_name(http_method, rule)
        )
        operation = models.Operation(**operation_attrs)
        self.__marker_map[operation.operation_id] = marker

        if (request_body := marker.request_body) is not None:
            ref = self._add_model(request_body.type)
            operation.request_body = models.RequestBody(
                description=request_body.metadata.openapi['description'],
                content={'application/json': models.Content(schema=ref)},
                required=True,
            )
        if marker.response_type is not None:
            ref = self._add_model(marker.response_type)
            status_code = marker.extra.get('default_status', 200)
            if not isinstance(status_code, int):
                raise TypeError(
                    f'status_code must be an int, not {type(status_code)}'
                )
            try:
                description = http.client.responses[status_code]
            except KeyError:
                description = f'Unknown HTTP {status_code}'
            operation.responses[str(status_code)] = models.Response(
                description=description,
                content={'application/json': models.Content(schema=ref)},
            )

        for status_code, defn in marker.errors.items():
            if model := defn.get('model'):
                ref = self._add_model(model)
            elif default := self.__default_error_models.get(status_code):
                ref = default
            else:
                self.__defaulted_errors[status_code].append((operation, defn))
                continue
            operation.responses[str(status_code)] = models.Response(
                description=defn.get(
                    'description',
                    http.client.responses.get(
                        status_code, f'Unknown HTTP {status_code}'
                    ),
                ),
                content={'application/json': models.Content(schema=ref)},
            )

        for status_code in self.__global_status_codes:
            if str(status_code) in operation.responses:
                continue
            default = self.__default_error_models[status_code]
            operation.responses[str(status_code)] = models.Response(
                description=http.client.responses.get(
                    status_code, f'Unknown HTTP {status_code}'
                ),
                content={'application/json': models.Content(schema=default)},
            )

        for name, header_def in marker.headers.items():
            schema_or_ref: models.Schema | models.Reference
            if model := header_def.get('model'):
                schema_or_ref = self._add_model(model)
            else:
                schema_or_ref = models.Schema.model_validate(
                    {
                        'type': 'string',
                    }
                )
            header = models.ResponseHeader.model_validate(
                {'schema': schema_or_ref, **header_def}
            )
            status_codes: list[str]
            if code_or_list := header_def.get('for_status'):
                if isinstance(code_or_list, int):
                    status_codes = [str(code_or_list)]
                else:
                    status_codes = [str(code) for code in code_or_list]
            else:
                status_codes = list(operation.responses.keys())

            self.logger.debug(
                'Adding header %s to status codes %r',
                name,
                status_codes,
            )
            for code in status_codes:
                try:
                    response = operation.responses[code]
                except KeyError:
                    response = models.Response()
                    operation.responses[code] = response
                if response.headers is None:
                    response.headers = {}
                response.headers[name] = header

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

    def add_tag(
        self,
        name: str,
        description: str | None = None,
    ) -> models.Tag:
        """Add a tag to the document.

        :raises ValueError: if a tag with the same name exists

        """
        if name in {tag.name for tag in self.openapi_doc.tags}:
            raise errors.DuplicateTagError(name)
        new_tag = models.Tag(name=name, description=description)
        self.openapi_doc.tags.append(new_tag)
        return new_tag

    def get_operation(
        self, rule: routing.Rule, http_method: str
    ) -> models.Operation:
        if not isinstance(rule, routing.URLSpec):
            warnings.warn(
                f'{rule.__class__.__name__} rules are not supported',
                UserWarning,
                stacklevel=2,
            )
            raise TypeError(
                f'get_operation() expected Rule, got {rule.__class__.__name__}'
            )

        working = rule.regex.pattern.removesuffix('$')
        parsed = _generate_openapi_path(working)
        path_item = self.openapi_doc.paths.get(parsed.path)

        if path_item is None:
            raise errors.OperationNotFoundError(parsed.path, http_method)

        method = getattr(path_item, http_method.lower(), None)
        if method is None:
            raise errors.OperationNotFoundError(parsed.path, http_method)

        return typing.cast(models.Operation, method)

    def tag_operation(
        self, rule: routing.Rule, http_method: str, *tags: models.Tag | str
    ) -> None:
        """Tag an operation.

        This method will add `Tag` instances from the `tags` argument
        to the document. If a `str` is passed, it will search for the
        tag in the document. If the tag is not found, raises a
        `ValueError`.

        :raises ValueError: if a tag string is not found
        :seealso: `add_tag`

        """
        all_tags = {t.name: t for t in self.openapi_doc.tags}
        operation = self.get_operation(rule, http_method)
        for tag in tags:
            tag_name = tag if isinstance(tag, str) else tag.name
            try:
                tag_instance = all_tags[tag_name]
            except KeyError:
                if isinstance(tag, models.Tag):
                    self.openapi_doc.tags.append(tag)
                    all_tags[tag_name] = tag
                    tag_instance = tag
                else:
                    raise errors.TagNotFoundError(tag_name) from None

            operation.tags.append(tag_instance.name)

    def set_default_error_model(
        self,
        status_code: int,
        model: type[pydantic.BaseModel],
        *,
        description: str | None = None,
    ) -> models.Reference:
        self.logger.debug(
            'Setting default error model for %s to %s',
            status_code,
            model.__name__,
        )
        ref = self._add_model(model)
        description = (
            f'Unknown HTTP {status_code}'
            if description is None
            else description
        )
        for operation, defn in self.__defaulted_errors[status_code]:
            response = models.Response(
                description=defn.get('description', description),
                content={'application/json': models.Content(schema=ref)},
            )
            operation.responses[str(status_code)] = response
            marker = self.__marker_map[operation.operation_id]
            for name, header_def in marker.headers.items():
                status_codes: list[str]
                if code_or_list := header_def.get('for_status'):
                    if isinstance(code_or_list, int):
                        status_codes = [str(code_or_list)]
                    else:
                        status_codes = [str(code) for code in code_or_list]
                else:
                    status_codes = list(operation.responses.keys())
                if str(status_code) not in status_codes:
                    continue

                schema_or_ref: models.Schema | models.Reference
                if header_model := header_def.get('model'):
                    schema_or_ref = self._add_model(header_model)
                else:
                    schema_or_ref = models.Schema.model_validate(
                        {
                            'type': 'string',
                        }
                    )
                header = models.ResponseHeader.model_validate(
                    {'schema': schema_or_ref, **header_def}
                )
                if response.headers is None:
                    response.headers = {}
                response.headers[name] = header

        self.__defaulted_errors.pop(status_code)
        self.__default_error_models[status_code] = ref

        return ref

    def _add_model(self, model: type[pydantic.BaseModel]) -> models.Reference:
        try:
            ref = self.__model_map[model]
        except KeyError:
            pydantic_schema = model.model_json_schema(
                ref_template='#/components/schemas/{model}',
                mode='serialization',
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


class ValidationErrorDetail(pydantic.BaseModel):
    type: str = pydantic.Field(
        description=(
            'The type of error that occurred, this identifier is '
            'designed for programmatic use.'
        ),
        examples=['enum'],
    )
    msg: str = pydantic.Field(
        description='A human-readable message describing the error.',
        examples=["Input should be 'solo', 'demi' or 'grande"],
    )
    loc: tuple[int | str, ...] = pydantic.Field(
        description=(
            'Tuple of strings and integers identifying where in '
            'the schema the error occurred.'
        ),
        examples=[1, 'size'],
    )


class ValidationError(pydantic.BaseModel):
    """Use with register_error_model to document validation errors.

    This is the structure of errors returned by handlers.ResponseFormatter.
    Register this model as the default response for "unprocessable entity"
    to include it in the generated OpenAPI specification.
    ```
    class Application(handlers.OpenAPIApplication):
        def __init__(self, **settings: object):
            super().__init__([web.url(r'/items', MyHandler)], **settings)
            self.register_error_model(422, openapi.ValidationError)
    ```

    Then use it in your handlers that accept a body parameter to specify
    that they may respond with an "unprocessable entity" error.
    ```
    class MyHandler(handlers.RequestHandler):
        @api.expose_operation
        @api.add_error_response(422)
        async def post(self, *,
                       body: typing.Annotated[Create, api.Body]) -> MyModel:
            ...
    ```

    """

    status: int = pydantic.Field(
        description='HTTP status code', examples=[422]
    )
    title: str = pydantic.Field(
        description='Generic description of the failure',
        examples=['Unprocessable Entity'],
    )
    detail: str = pydantic.Field(
        description='Specific description of the failure',
        examples=["Input should be 'solo', 'demi' or 'grande'"],
    )
    errors: list[ValidationErrorDetail] | None = None


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
    if typing.get_origin(anno) is typing.Annotated:
        anno = typing.get_args(anno)[0]

    schema = {}
    if anno is types.NoneType or anno is None:
        schema['type'] = 'null'
    elif issubclass(anno, pydantic.BaseModel):
        schema.update(anno.model_json_schema())
    elif issubclass(anno, bool):
        schema['type'] = 'boolean'
    elif issubclass(anno, int):
        schema.update({'type': 'number', 'format': 'int'})
    elif issubclass(anno, float):
        schema['type'] = 'number'
    elif issubclass(anno, str):
        schema['type'] = 'string'

    if schema:
        return models.Schema.model_validate(schema)

    raise RuntimeError(f'Unsupported type: {anno}')


def _snake_case_operation_name(http_method: str, rule: routing.Rule) -> str:
    cls_name = re.sub(r'(?<!^)(?=[A-Z])', '_', rule.target.__name__).lower()
    return f'{cls_name}_{http_method.lower()}'
