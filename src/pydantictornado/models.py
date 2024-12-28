import enum
import typing
from collections import abc

import pydantic


class AlwaysIn(set[str]):
    def __contains__(self, item_: object) -> bool:
        return True


class FieldOmittingMixin(pydantic.BaseModel):
    OMIT_IF_EMPTY: typing.ClassVar[tuple[str, ...] | None] = None
    OMIT_IF_NONE: typing.ClassVar[tuple[str, ...] | None] = None

    @pydantic.model_serializer(mode='wrap')
    def _omit_fields(
        self, handler: pydantic.SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        omit_if_empty = (
            AlwaysIn() if self.OMIT_IF_EMPTY is None else self.OMIT_IF_EMPTY
        )
        omit_if_none = (
            AlwaysIn() if self.OMIT_IF_NONE is None else self.OMIT_IF_NONE
        )
        result = typing.cast(dict[str, object], handler(self))
        for name, info in self.model_fields.items():
            key = (
                info.serialization_alias if info.serialization_alias else name
            )
            value = result.get(key, None)
            empty = isinstance(value, abc.Sized) and len(value) == 0
            if (value is None and name in omit_if_none) or (
                empty and name in omit_if_empty
            ):
                result.pop(key, None)
        return result


class Contact(FieldOmittingMixin, pydantic.BaseModel):
    name: str | None
    url: pydantic.HttpUrl | None
    email: str | None


class License(FieldOmittingMixin, pydantic.BaseModel):
    name: str
    url: pydantic.HttpUrl | None


class Info(FieldOmittingMixin, pydantic.BaseModel):
    title: str = 'API Documentation'
    description: str | None = None
    terms_of_service: str | None = pydantic.Field(
        None, serialization_alias='termsOfService'
    )
    contact: Contact | None = None
    license: License | None = None
    version: str = '0.0.0'


class ServerVariable(FieldOmittingMixin, pydantic.BaseModel):
    enum: list[str] | None
    default: str
    description: str | None


class Server(FieldOmittingMixin, pydantic.BaseModel):
    url: str
    description: str | None
    variables: dict[str, ServerVariable] | None


class ExternalDocumentation(FieldOmittingMixin, pydantic.BaseModel):
    description: str | None
    url: pydantic.HttpUrl


class Tag(FieldOmittingMixin, pydantic.BaseModel):
    name: str
    description: str | None
    external_docs: ExternalDocumentation | None = pydantic.Field(
        None, serialization_alias='externalDocs'
    )


class Reference(FieldOmittingMixin, pydantic.BaseModel):
    ref: str = pydantic.Field(..., serialization_alias='$ref')


class SchemaTypeString(enum.StrEnum):
    ARRAY = 'array'
    BOOLEAN = 'boolean'
    NULL = 'null'
    NUMBER = 'number'
    OBJECT = 'object'
    STRING = 'string'


class Schema(FieldOmittingMixin, pydantic.BaseModel):
    model_config = {'extra': 'allow'}
    type: SchemaTypeString | abc.Sequence[SchemaTypeString]


class ParameterLocation(enum.StrEnum):
    COOKIE = 'cookie'
    HEADER = 'header'
    QUERY = 'query'
    PATH = 'path'


class ParameterStyle(enum.StrEnum):
    DEEP_OBJECT = 'deepObject'
    FORM = 'form'
    LABEL = 'label'
    MATRIX = 'matrix'
    PIPE_DELIMITED = 'pipeDelimited'
    SIMPLE = 'simple'
    SPACE_DELIMITED = 'spaceDelimited'


class Parameter(FieldOmittingMixin, pydantic.BaseModel):
    name: str
    in_: ParameterLocation = pydantic.Field(..., alias='in')
    description: str | None = None
    required: bool
    deprecated: bool = False
    schema_: Schema | Reference = pydantic.Field(..., alias='schema')
    style: ParameterStyle

    @pydantic.model_validator(mode='before')
    @classmethod
    def set_defaults_based_on_parameter_location(
        cls, data: dict[str, object]
    ) -> dict[str, object]:
        if isinstance(data, dict):
            location = typing.cast(ParameterLocation, data['in'])
            if 'required' not in data:
                data['required'] = location == ParameterLocation.PATH
            if 'style' not in data:
                match location:
                    case ParameterLocation.COOKIE:
                        style = ParameterStyle.FORM
                    case ParameterLocation.HEADER:
                        style = ParameterStyle.SIMPLE
                    case ParameterLocation.PATH:
                        style = ParameterStyle.SIMPLE
                    case ParameterLocation.QUERY:
                        style = ParameterStyle.FORM
                    case _ as unreachable:  # pragma: nocover
                        typing.assert_never(unreachable)
                data['style'] = style
        return data

    @pydantic.model_validator(mode='after')
    def verify(self) -> typing.Self:
        if self.in_ == ParameterLocation.PATH and not self.required:
            raise ValueError('path parameters must be required')
        return self


class Content(FieldOmittingMixin, pydantic.BaseModel):
    schema_: Schema | Reference = pydantic.Field(..., alias='schema')


class Response(FieldOmittingMixin, pydantic.BaseModel):
    description: str = ''
    headers: dict[str, Schema | Reference] | None = None
    content: dict[str, Content] | None = None
    links: dict[str, typing.Union['Link', Reference]] | None = None


class RequestBody(FieldOmittingMixin, pydantic.BaseModel):
    description: str | None
    content: dict[str, Content]
    required: bool | None


class Operation(FieldOmittingMixin, pydantic.BaseModel):
    tags: list[str] = pydantic.Field(default_factory=list[str])
    summary: str | None = None
    description: str | None = None
    external_docs: ExternalDocumentation | None = pydantic.Field(
        None, serialization_alias='externalDocs'
    )
    operation_id: str | None = pydantic.Field(
        None, serialization_alias='operationId'
    )
    parameters: list[Parameter | Reference] | None = pydantic.Field(
        default_factory=list[Parameter | Reference]
    )
    request_body: RequestBody | Reference | None = pydantic.Field(
        None, serialization_alias='requestBody'
    )
    responses: dict[str, Response] = pydantic.Field(
        default_factory=dict[str, Response]
    )
    callbacks: dict[str, typing.Union['Callback', Reference]] | None = (
        pydantic.Field(
            default_factory=dict[str, typing.Union['Callback', Reference]]
        )
    )
    deprecated: bool | None = None
    security: list[dict[str, list[str]]] | None = pydantic.Field(
        default_factory=list[dict[str, list[str]]]
    )
    servers: list[Server] | None = pydantic.Field(default_factory=list[Server])


class PathItem(FieldOmittingMixin, pydantic.BaseModel):
    ref: str | None = pydantic.Field(None, serialization_alias='$ref')
    summary: str | None = None
    description: str | None = None
    get: Operation | None = None
    put: Operation | None = None
    post: Operation | None = None
    delete: Operation | None = None
    options: Operation | None = None
    head: Operation | None = None
    patch: Operation | None = None
    trace: Operation | None = None
    servers: list[Server] = pydantic.Field(default_factory=list[Server])
    parameters: list[Parameter | Reference] = pydantic.Field(
        default_factory=list[Parameter | Reference]
    )


class Components(FieldOmittingMixin, pydantic.BaseModel):
    schemas: dict[str, Schema] = pydantic.Field(
        default_factory=dict[str, Schema]
    )
    responses: dict[str, Response] = pydantic.Field(
        default_factory=dict[str, Response]
    )
    parameters: dict[str, Parameter] = pydantic.Field(
        default_factory=dict[str, Parameter]
    )
    examples: dict[str, 'Example'] = pydantic.Field(
        default_factory=dict[str, 'Example']
    )
    request_bodies: dict[str, RequestBody] = pydantic.Field(
        default_factory=dict[str, RequestBody],
        serialization_alias='requestBodies',
    )
    headers: dict[str, Schema] = pydantic.Field(
        default_factory=dict[str, Schema]
    )
    security_schemes: (
        dict[str, typing.Union['SecurityScheme', Reference]] | None
    ) = pydantic.Field(None, serialization_alias='securitySchemes')
    links: dict[str, typing.Union['Link', Reference]] | None = pydantic.Field(
        default_factory=dict[str, typing.Union['Link', Reference]]
    )
    callbacks: dict[str, typing.Union['Callback', Reference]] | None = (
        pydantic.Field(
            default_factory=dict[str, typing.Union['Callback', Reference]]
        )
    )


class SecurityRequirement(FieldOmittingMixin, pydantic.BaseModel):
    security_scheme: list[str] = pydantic.Field(
        ..., serialization_alias='securityScheme'
    )


class OpenAPI(FieldOmittingMixin, pydantic.BaseModel):
    openapi: str = '3.1.0'
    info: Info = pydantic.Field(default_factory=Info)
    servers: list[Server] = pydantic.Field(default_factory=list[Server])
    paths: dict[str, PathItem] = pydantic.Field(
        default_factory=dict[str, PathItem]
    )
    components: Components = pydantic.Field(default_factory=Components)
    security: list[SecurityRequirement] = pydantic.Field(
        default_factory=list[SecurityRequirement]
    )
    tags: list[Tag] = pydantic.Field(default_factory=list[Tag])
    external_docs: ExternalDocumentation | None = pydantic.Field(
        None, serialization_alias='externalDocs'
    )


class Example(FieldOmittingMixin, pydantic.BaseModel):
    summary: str | None
    description: str | None
    value: dict[str, object] | list[object] | str | int | float | bool | None
    external_value: pydantic.HttpUrl | None = pydantic.Field(
        None, serialization_alias='externalValue'
    )


class Link(FieldOmittingMixin, pydantic.BaseModel):
    operation_ref: str | None = pydantic.Field(
        None, serialization_alias='operationRef'
    )
    operation_id: str | None = pydantic.Field(
        None, serialization_alias='operationId'
    )
    parameters: (
        dict[str, str | int | bool | list[object] | dict[str, object]] | None
    )
    request_body: (
        str | int | bool | list[object] | dict[str, object] | None
    ) = pydantic.Field(None, serialization_alias='requestBody')
    description: str | None
    server: Server | None


class Callback(FieldOmittingMixin, pydantic.BaseModel):
    callback: dict[str, PathItem]


class SecurityScheme(FieldOmittingMixin, pydantic.BaseModel):
    type: str
    name: str | None
    in_: str | None = pydantic.Field(None, alias='in')
    scheme: str | None
    bearer_format: str | None = pydantic.Field(
        None, serialization_alias='bearerFormat'
    )
    flows: dict[str, 'OAuthFlow'] | None
    open_id_connect_url: pydantic.HttpUrl | None = pydantic.Field(
        None, serialization_alias='openIdConnectUrl'
    )


class OAuthFlow(FieldOmittingMixin, pydantic.BaseModel):
    authorization_url: pydantic.HttpUrl | None = pydantic.Field(
        None, serialization_alias='authorizationUrl'
    )
    token_url: pydantic.HttpUrl | None = pydantic.Field(
        None, serialization_alias='tokenUrl'
    )
    refresh_url: pydantic.HttpUrl | None = pydantic.Field(
        None, serialization_alias='refreshUrl'
    )
    scopes: dict[str, str]
