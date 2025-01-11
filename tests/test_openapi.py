import typing
import unittest.mock

import pydantic
import tornado.httputil
import tornado.routing
import tornado.web

import tests
from pydantictornado import api, errors, models, openapi


class AutoInitializingHandler(tornado.web.RequestHandler):
    def __init__(self) -> None:
        request = tornado.httputil.HTTPServerRequest()
        request.connection = unittest.mock.Mock()
        super().__init__(tornado.web.Application(), request)


class UndecoratedHandler(AutoInitializingHandler):
    async def get(self) -> None:
        pass

    async def post(self, body: pydantic.BaseModel) -> None:
        pass


class RequestModel(pydantic.BaseModel):
    name: str


class ResponseModel(pydantic.BaseModel):
    id: int
    name: str


class ErrorModel(pydantic.BaseModel):
    status_code: int
    title: str


class DecoratedHandler(AutoInitializingHandler):
    @api.expose_operation
    async def get(self, item_id: int) -> None:  # noqa: ARG002
        return None

    @api.expose_operation
    async def post(
        self, body: typing.Annotated[RequestModel, api.Body]
    ) -> ResponseModel:
        return ResponseModel(id=42, name=body.name)


class CorrelationID(pydantic.RootModel[str]):
    root: str = pydantic.Field(
        alias='correlation-id',
        pattern='[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}',
    )


class AddOperationTest(tests.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.doc = openapi.OpenAPIDocument()

    def add_operation(
        self,
        path: str,
        method: str,
        handler_cls: type[tornado.web.RequestHandler],
    ) -> models.Operation:
        rule = tornado.routing.URLSpec(path, handler_cls)
        self.doc.add_operation(
            method.upper(), rule, getattr(handler_cls, method.lower())
        )
        return self.doc.get_operation(rule, method.upper())


class TestAddOperation(AddOperationTest):
    def test_add_operation_without_marker(self) -> None:
        rule = tornado.routing.URLSpec(r'/test', UndecoratedHandler)
        self.doc.add_operation('GET', rule, UndecoratedHandler.get)

        self.assertEqual(len(self.doc.openapi_doc.paths), 0)
        with self.assertRaises(errors.OperationNotFoundError):
            self.doc.get_operation(rule, 'GET')

    def test_add_operation_with_body_and_response(self) -> None:
        self.add_operation(r'/test', 'POST', DecoratedHandler)

        self.assertEqual(len(self.doc.openapi_doc.paths), 1)
        path_item = self.doc.openapi_doc.paths['/test']
        self.assertIsNotNone(path_item.post)
        self.assertEqual(
            path_item.post.operation_id,
            'decorated_handler_post',
        )
        self.assertIsNotNone(path_item.post.request_body)
        self.assertEqual(
            path_item.post.request_body.content[
                'application/json'
            ].schema_.ref,
            '#/components/schemas/RequestModel',
        )

        rsp = path_item.post.responses['200']
        content = rsp.content['application/json']  # type: ignore[index]  # mypy#4063
        self.assertEqual(
            content.schema_.ref,
            '#/components/schemas/ResponseModel',
        )

    def test_add_operation_path_params(self) -> None:
        self.add_operation(
            r'/test/(?P<item_id>[^/]+)', 'GET', DecoratedHandler
        )
        self.assertEqual(len(self.doc.openapi_doc.paths), 1)
        self.assertIn('/test/{item_id}', self.doc.openapi_doc.paths)

    def test_add_operation_with_invalid_default_status(self) -> None:
        rule = tornado.routing.URLSpec(r'/test', DecoratedHandler)
        marker = api.OpenAPIMethodInfo.extract(DecoratedHandler.post)
        try:
            marker.extra['default_status'] = 'not-a-number'
            with self.assertRaises(TypeError):
                self.doc.add_operation('POST', rule, DecoratedHandler.post)

            marker.extra['default_status'] = 600
            try:
                self.doc.add_operation('POST', rule, DecoratedHandler.post)
            except Exception:  # noqa: BLE001
                self.fail(
                    'add_operation should not fail with unknown status code'
                )

        finally:
            marker.extra.pop('default_status')

    def test_add_operation_with_additional_status_codes(self) -> None:
        self.doc.set_default_error_model(404, ErrorModel)
        test_data: dict[int, api.ErrorResponseDefinition] = {
            400: {'description': 'Bad Request', 'model': ErrorModel},
            404: {'description': 'Not Found'},
            409: {},
            500: {'model': ErrorModel},
            600: {'model': ErrorModel},
        }

        def assert_error_response(
            operation: models.Operation, status_code: int, description: str
        ) -> None:
            try:
                response = operation.responses[str(status_code)]
            except KeyError:
                self.fail(f'No response for status code {status_code}')
            self.assertEqual(description, response.description)

            try:
                content = response.content['application/json']  # type: ignore[index]
            except KeyError:
                self.fail('Response does not have application/json content')
            self.assertIsInstance(content.schema_, models.Reference)
            self.assertEqual(
                content.schema_.ref, '#/components/schemas/ErrorModel'
            )

        rule = tornado.routing.URLSpec(r'/test', DecoratedHandler)
        marker = api.OpenAPIMethodInfo.extract(DecoratedHandler.post)
        with unittest.mock.patch.object(marker, 'errors', test_data):
            self.doc.add_operation('POST', rule, DecoratedHandler.post)
            operation = self.doc.get_operation(rule, 'POST')

            assert_error_response(operation, 400, 'Bad Request')
            assert_error_response(operation, 404, 'Not Found')
            assert_error_response(operation, 500, 'Internal Server Error')
            assert_error_response(operation, 600, 'Unknown HTTP 600')

        self.assertIn(409, self.doc._OpenAPIDocument__defaulted_errors)  # type: ignore[attr-defined]

    def test_set_default_error_model(self) -> None:
        class Handler(AutoInitializingHandler):
            @api.expose_operation
            @api.add_error_response(404)
            async def get(self, item_id: int) -> ResponseModel:
                return ResponseModel(id=item_id, name='test')

        operation = self.add_operation(r'/(?P<item_id>.*)', 'GET', Handler)
        self.assertIsNone(operation.responses.get('404'))

        self.doc.set_default_error_model(404, ErrorModel)
        self.assertIsNotNone(operation.responses.get('404'))

    def test_add_response_headers(self) -> None:
        class Handler(AutoInitializingHandler):
            @api.expose_operation(default_status=201)
            @api.add_error_response(500)
            @api.add_response_header('correlation-id', model=CorrelationID)
            @api.add_response_header('location', for_status=201)
            @api.add_response_header('retry-after', for_status=[500, 503])
            async def post(
                self, *, body: typing.Annotated[RequestModel, api.Body]
            ) -> ResponseModel:
                return ResponseModel(id=42, name=body.name)

        operation = self.add_operation('/items', 'POST', Handler)
        self.doc.set_default_error_model(500, ErrorModel)

        self.assertEqual(
            self.doc.openapi_doc.components.schemas['CorrelationID'],
            models.Schema.model_validate(
                CorrelationID.model_json_schema(mode='serialization')
            ),
            'CorrelationID schema should be added to components.schemas',
        )

        response = self.unwrap(operation.responses.get('201'))
        headers = self.unwrap(response.headers)
        self.assertIn('correlation-id', headers)
        self.assertIn('location', headers)
        self.assertNotIn(
            'retry-after',
            headers,
            'Retry-After header should not be present for 201 response',
        )

        response = self.unwrap(operation.responses.get('500'))
        headers = self.unwrap(response.headers)
        self.assertIn('correlation-id', headers)
        self.assertNotIn(
            'location',
            headers,
            'Location header should only be present for 201 response',
        )
        self.assertIn('retry-after', headers)


class AddModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = openapi.OpenAPIDocument()

    def test_add_model_caches_reference(self) -> None:
        class TestModel(pydantic.BaseModel):
            name: str

        ref1 = self.doc._add_model(TestModel)
        ref2 = self.doc._add_model(TestModel)

        self.assertEqual(ref1.ref, '#/components/schemas/TestModel')
        self.assertIs(ref1, ref2)

    def test_add_model_adds_schema(self) -> None:
        class TestModel(pydantic.BaseModel):
            name: str
            count: int

        ref = self.doc._add_model(TestModel)
        self.assertEqual(ref.ref, '#/components/schemas/TestModel')
        schema = self.doc.openapi_doc.components.schemas['TestModel']
        props = typing.cast(dict[str, dict[str, object]], schema.properties)  # type: ignore[attr-defined]
        self.assertEqual(props['name']['type'], 'string')
        self.assertEqual(props['count']['type'], 'integer')

    def test_add_model_with_nested_models(self) -> None:
        class NestedModel(pydantic.BaseModel):
            value: str

        class TestModel(pydantic.BaseModel):
            nested: NestedModel

        ref = self.doc._add_model(TestModel)
        self.assertEqual(ref.ref, '#/components/schemas/TestModel')
        self.assertIn('TestModel', self.doc.openapi_doc.components.schemas)
        self.assertIn('NestedModel', self.doc.openapi_doc.components.schemas)

    def test_add_model_with_excluded_fields(self) -> None:
        class TestModel(pydantic.BaseModel):
            index: int = pydantic.Field(exclude=True)
            max: int = pydantic.Field(exclude=True)

            @pydantic.computed_field
            def detail(self) -> str:
                return f'{self.index} of {self.max}'

        ref = self.doc._add_model(TestModel)
        self.assertEqual(ref.ref, '#/components/schemas/TestModel')
        self.assertIn('TestModel', self.doc.openapi_doc.components.schemas)
        schema = self.doc.openapi_doc.components.schemas['TestModel']
        props = typing.cast(dict[str, dict[str, object]], schema.properties)  # type: ignore[attr-defined]
        self.assertIn('detail', props)
        self.assertNotIn('index', props)
        self.assertNotIn('max', props)


class TinyIdTests(unittest.TestCase):
    def test_default_length(self) -> None:
        tiny_id = openapi._generate_tiny_id()
        self.assertEqual(
            len(tiny_id), 8, 'Default length should be 8 characters'
        )

    def test_custom_length(self) -> None:
        tiny_id = openapi._generate_tiny_id(length=16)
        self.assertEqual(
            len(tiny_id), 16, 'Custom length should be 16 characters'
        )

    def test_valid_characters(self) -> None:
        tiny_id = openapi._generate_tiny_id(length=100)
        valid_chars = set(openapi._TINY_ID_CHARS)
        for char in tiny_id:
            self.assertIn(
                char,
                valid_chars,
                f'Character {char} should be in the valid character set',
            )

    def test_uniqueness(self) -> None:
        ids = {openapi._generate_tiny_id() for _ in range(100)}
        self.assertEqual(len(ids), 100, 'All generated IDs should be unique')

    def test_zero_length(self) -> None:
        tiny_id = openapi._generate_tiny_id(length=0)
        self.assertEqual(
            tiny_id, '', 'Zero length should result in an empty string'
        )


class SchemaGenerationTests(unittest.TestCase):
    def test_none_type(self) -> None:
        schema = openapi._generate_schema(None)
        self.assertEqual(schema.type, 'null')

    def test_bool_type(self) -> None:
        schema = openapi._generate_schema(bool)
        self.assertEqual(schema.type, 'boolean')

    def test_float_type(self) -> None:
        schema = openapi._generate_schema(float)
        self.assertEqual(schema.type, 'number')

    def test_int_type(self) -> None:
        schema = openapi._generate_schema(int)
        self.assertEqual(schema.type, 'number')
        self.assertEqual(schema.format, 'int')  # type: ignore[attr-defined]

    def test_str_type(self) -> None:
        schema = openapi._generate_schema(str)
        self.assertEqual(schema.type, 'string')

    def test_pydantic_model(self) -> None:
        class TestModel(pydantic.BaseModel):
            name: str
            count: int

        schema = openapi._generate_schema(TestModel)
        self.assertEqual(schema.type, 'object')
        self.assertIn('name', schema.properties)  # type: ignore[attr-defined]
        self.assertIn('count', schema.properties)  # type: ignore[attr-defined]
        self.assertEqual(schema.properties['name']['type'], 'string')  # type: ignore[attr-defined]
        self.assertEqual(schema.properties['count']['type'], 'integer')  # type: ignore[attr-defined]

    def test_annotated_type(self) -> None:
        schema = openapi._generate_schema(typing.Annotated[str, api.Body])  # type: ignore[arg-type]
        self.assertEqual(schema.type, 'string')

    def test_unsupported_type(self) -> None:
        with self.assertRaises(RuntimeError):
            openapi._generate_schema(object)


class GetOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.doc = openapi.OpenAPIDocument()

    def test_get_operation_on_unsupported_type(self) -> None:
        with self.assertRaises(TypeError), self.assertWarns(UserWarning):
            self.doc.get_operation(object(), 'GET')  # type: ignore[arg-type]

    def test_get_operation_on_nonexistent_path(self) -> None:
        with self.assertRaises(errors.OperationNotFoundError) as context:
            self.doc.get_operation(
                tornado.routing.URLSpec(r'/test', UndecoratedHandler),
                'GET',
            )
        self.assertEqual(context.exception.http_method, 'GET')
        self.assertEqual(context.exception.path_expression, '/test')

    def test_get_operation_on_nonexistent_method(self) -> None:
        rule = tornado.routing.URLSpec(r'/(?P<item_id>.*)', DecoratedHandler)
        self.doc.add_operation('GET', rule, DecoratedHandler.get)
        with self.assertRaises(errors.OperationNotFoundError) as context:
            self.doc.get_operation(rule, 'POST')
        self.assertEqual(context.exception.http_method, 'POST')
        self.assertEqual(context.exception.path_expression, '/{item_id}')


class GlobalErrorTests(tests.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.doc = openapi.OpenAPIDocument()

    def test_add_global_error_with_path_details(self) -> None:
        self.doc.add_operation(
            'GET',
            tornado.routing.URLSpec(r'/test/(?P<item_id>)', DecoratedHandler),
            DecoratedHandler.get,
        )
        path_item = self.doc.openapi_doc.paths['/test/{item_id}']
        path_item.summary = 'Cannot be set by a decorator... yet'

        self.doc.add_global_error(500, ErrorModel)
        self.assertIn('500', self.unwrap(path_item.get).responses)

    def test_add_operation_replacing_global_error(self) -> None:
        class Handler(AutoInitializingHandler):
            @api.expose_operation
            @api.add_error_response(500, description='Replaces global error')
            async def get(self) -> None:
                pass

        self.doc.add_global_error(500, ErrorModel)
        self.doc.add_operation(
            'GET',
            tornado.routing.URLSpec(r'/test', Handler),
            Handler.get,
        )
        path_item = self.doc.openapi_doc.paths['/test']
        response = self.unwrap(path_item.get).responses['500']
        self.assertEqual(response.description, 'Replaces global error')
