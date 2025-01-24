# API Reference

The Pydantic Tornado API is more complex than I would like it to be, but it is also very powerful. The combination of compile-time
and runtime behavior makes the API rather complex to reason about. Hopefully, this section will help you understand how to use the API.
The snippets in this section refer back to the example application presented in the main page.

## Decorators

!!! note

    The `add_error_response` and `add_response_header` decorators **do not** insert headers or influence the response sent
    to the client. They only add metadata to the OpenAPI specification. The request handler is responsible for calling
    [self.set_header][tornado.web.RequestHandler.set_header] to set the header content.

The [api.expose_operation][pydantictornado.api.expose_operation] decorator exposes a method as an operation in the OpenAPI
specification. It also validates the request body and other parameters based on type annotations and then passes the validated
values to the method. The method is required to be an async method and it should return a model instance or `None`. The
model schema for the body and the "successful" return value are taken directly from the Pydantic models used in the type
annotations. You can influence the schema definition using Pydantic's JSON schema generation features.

The [api.add_error_response][pydantictornado.api.add_error_response] decorator adds an error response to the operation. Each
decorator call adds a new error response to [the `responses` collection](https://spec.openapis.org/oas/v3.1.1.html#responses-object)
of the OpenAPI operation. You can include multiple error responses for a single operation by stacking decorators. This decorator
works in conjunction with the [register_error_model][pydantictornado.handlers.OpenAPIApplication.register_error_model] method
of the application. You can specify the structure (schema) of the error body using the `model` keyword argument of the decorator
or by calling the `register_error_model` method during application initialization.

???+ tip

    You should default to describing error structure using the [register_error_model][pydantictornado.handlers.OpenAPIApplication.register_error_model]
    method. The `model` keyword argument of the [api.add_error_response][pydantictornado.api.add_error_response] decorator should
    only be used for responses that are specific to a single operation.

The [api.add_response_header][pydantictornado.api.add_response_header] decorator adds a response header to the specification.
Most response headers have a simple structure, but you can use the `model` parameter to specify a more complex structure if
necessary. The `for_status` keyword can be used to specify the status code for which the header is relevant. If the `for_status`
is unspecified, then the header is added to every response for the operation. It can be set to a single status code or a list
of status codes.

```python
class CollectionHandler(RequestHandler):
    @api.expose_operation(summary='Create a new Item', default_status=201)
    @api.add_error_response(422)
    @api.add_response_header('location', for_status=201)
    async def post(
        self, *, body: typing.Annotated[CreationRequest, api.Body]
    ) -> Item:
        new_item = Item(id=uuid.uuid4(), **body.model_dump(mode='python'))
        self.application.db[new_item.id] = new_item
        self.set_header('location', self.reverse_url('item_handler', new_item.id))
        return new_item
```

## Usage of typing.Annotated

This library makes use of [typing.Annotated][] to attach OpenAPI metadata to types and to mark parameters for runtime processing.
I adopted this approach after using FastAPI's dependency injection system. The use of annotations frees you to name and arrange
your method parameters freely. In essence, this is what makes your request handling methods feel like regular Python methods
where you are in complete control of the signature. The magic happens inside the erapper provided by the `api.expose_operation`
decorator so you can call the method exactly as it appears in the source code should you need to do so.

### Marking the request body

```python
class ItemHandler(RequestHandler):
    @api.expose_operation(summary='Update Item by ID')
    @api.add_error_response(404, description='Item Not Found')
    @api.add_error_response(422)
    async def put(
        self, item_id: str, *, body: typing.Annotated[Item, api.Body]
    ) -> Item:
        ...
```

The `body` parameter is marked with `typing.Annotated[Item, api.Body]`. This tells the API that the parameter should receive the
request body. The library will validate the request body against the `Item` model and pass the validated data to the method.
The pydantic model is also used to generate the OpenAPI schema for the request body. The `item_id` parameter is not marked with
a pydantic-tornado annotation so it is treated as a path parameter. The type hint is used as the schema for the path parameter
in the generated OpenAPI operation. Path parameters are passed as strings; however, the library converts the string value to the
hinted type.

!!! warning

    The library only supports `str`, `int`, and `float` path parameters. If you need to use a different type, you should use
    a string annotation and implement type conversion in the method body.

### Augmenting the OpenAPI specification

The second use of `typing.Annotated` is to augment the generated OpenAPI specification. You can pass an instance of
[api.Body][pydantictornado.api.Body] as an annotation to specify additional metadata for the request body. The description for
the request body is omitted by default. You can provide one by passing the `description` keyword to the `api.Body` initializer.

```python
class ItemHandler(RequestHandler):
    @api.expose_operation(summary='Update Item by ID')
    @api.add_error_response(404, description='Item Not Found')
    @api.add_error_response(422)
    async def put(
        self, item_id: str, *,
        body: typing.Annotated[Item, api.Body(description='The new item data')]
    ) -> Item:
        ...
```

You can also use the `api.Body` class in the MRO of your request type. This is occasionally useful to shorten the method signature.
It can also muddy the type model if you are not very careful; remember that the request body is also required to be a Pydantic model
so you are adding new fields by subclassing. Using inheritance usually implies an "is-a" relationship which is not the case here.

## Request bodies and response types

The request body and response types are specified using Pydantic models. The models are used to validate the request body and
generate the OpenAPI schema for the operation. If the request model does not validate the request body, the API will return a
[422 Unprocessable Entity](https://datatracker.ietf.org/doc/html/rfc4918#section-11.2) response code with a JSON body containing
the validation errors. The response body uses the [openapi.ValidationError][pydantictornado.openapi.ValidationError] model. The
library **does not** register the error model for you. You should add the [api.add_error_response][pydantictornado.api.add_error_response]
decorator to your method if you want to document the response code.

## OpenAPI specification and UI

The OpenAPI specification is generated from information stored on the `Application` instance which must include
[handlers.OpenAPIApplication][pydantictornado.handlers.OpenAPIApplication] in its MRO. The specification is made available by
installing a route using the [handlers.OpenAPISpecHandler][pydantictornado.handlers.OpenAPISpecHandler] handler. The
[handlers.OpenAPIDocHandler][pydantictornado.handlers.OpenAPIDocHandler] handler is also available to serve a
[Stoplight Elements](https://elements.stoplight.io/) documentation page. You are responsible for wiring the handlers together
in your application. I recommend subclassing the `Application` class and extending the initializer instead of using a factory
function.

```python
from pydantictornado import handlers
from tornado import web

class Application(handlers.OpenAPIApplication, web.Application):
    def __init__(self, *args, **kwargs):
        routes = [
            web.URLSpec(r'/openapi.json', handlers.OpenAPISpecHandler, name='openapi_spec'),
            web.URLSpec(r'/docs', handlers.OpenAPIDocHandler,
                        kwargs={'spec_handler_name': 'openapi_spec'}, name='openapi_docs'),
        ]
        super().__init__(routes, *args, **kwargs)
```
