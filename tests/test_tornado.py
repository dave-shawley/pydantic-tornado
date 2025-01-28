import http
import importlib.resources
import json
import re
import typing
import unittest.mock

import pydantic
import tornado.httputil
import tornado.routing
import tornado.template
import tornado.web

import tests
from pydantictornado import api, errors, handlers, models, openapi


class TestGenerateOpenAPIPath(unittest.TestCase):
    def test_simple_path(self) -> None:
        result = openapi._generate_openapi_path('/test')
        self.assertEqual(result.path, '/test')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

    def test_named_parameter(self) -> None:
        result = openapi._generate_openapi_path('/test/(?P<id>[^/]+)')
        self.assertEqual(result.path, '/test/{id}')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, ['id'])

    def test_unnamed_parameter(self) -> None:
        with unittest.mock.patch(
            'pydantictornado.openapi._generate_tiny_id',
            return_value='unnamed',
        ):
            result = openapi._generate_openapi_path('/test/([^/]+)')
            self.assertEqual(result.path, '/test/{unnamed}')
            self.assertEqual(result.positional_params, ['unnamed'])
            self.assertEqual(result.named_params, [])

    def test_comment_parameter(self) -> None:
        result = openapi._generate_openapi_path('/test/(?#comment)')
        self.assertEqual(result.path, '/test/')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

    def test_non_capturing_group(self) -> None:
        result = openapi._generate_openapi_path('/test/(?:group)')
        self.assertEqual(result.path, '/test/group')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

        with unittest.mock.patch(
            'pydantictornado.openapi._generate_tiny_id',
            return_value='randomId',
        ):
            result = openapi._generate_openapi_path('/test/(?:<(group)>)')
            self.assertEqual(
                result.path,
                '/test/<{randomId}>',
                'capturing group within non-capturing group failed',
            )
            self.assertEqual(result.positional_params, ['randomId'])
            self.assertEqual(result.named_params, [])

    def test_root_path(self) -> None:
        result = openapi._generate_openapi_path('/?')
        self.assertEqual(result.path, '/')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

    def test_invalid_qualifier(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            openapi._generate_openapi_path('/test/(?X123)')
        self.assertEqual(str(ctx.exception), 'Unsupported qualifier: ?X')

    def test_invalid_named_parameter(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            openapi._generate_openapi_path('/test/(?Pinvalid)')
        self.assertEqual(str(ctx.exception), 'Invalid regular expression')

    def test_multiple_named_parameters(self) -> None:
        result = openapi._generate_openapi_path(
            '/test/(?P<id>[^/]+)/(?P<name>[^/]+)'
        )
        self.assertEqual(result.path, '/test/{id}/{name}')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, ['id', 'name'])

    def test_numerical_path(self) -> None:
        result = openapi._generate_openapi_path('/[0-9]+')
        self.assertEqual(result.path, '/[0-9]+')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

        with unittest.mock.patch(
            'pydantictornado.openapi._generate_tiny_id',
            return_value='randomId',
        ):
            result = openapi._generate_openapi_path('/([0-9]+)')
            self.assertEqual(result.path, '/{randomId}')
            self.assertEqual(result.positional_params, ['randomId'])
            self.assertEqual(result.named_params, [])

    def test_path_cleanup(self) -> None:
        result = openapi._generate_openapi_path('/optional-slash/?')
        self.assertEqual(result.path, '/optional-slash')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

        result = openapi._generate_openapi_path('/terminator$')
        self.assertEqual(result.path, '/terminator')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

        result = openapi._generate_openapi_path('/optional-slash/?$')
        self.assertEqual(result.path, '/optional-slash')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, [])

    def test_backreference(self) -> None:
        result = openapi._generate_openapi_path('/test/(?P<id>[^/]+)(?P=id)')
        self.assertEqual(result.path, '/test/{id}{id}')
        self.assertEqual(result.positional_params, [])
        self.assertEqual(result.named_params, ['id'])

        with self.assertRaises(RuntimeError):
            openapi._generate_openapi_path('/test/(?P=comment)')


class CreateItemRequest(pydantic.BaseModel):
    name: str


class Item(pydantic.BaseModel):
    id: int
    name: str


class ErrorModel(pydantic.BaseModel):
    status: int
    title: str


class CreateItemHandler(handlers.PydanticErrorHandler):
    @api.expose_operation(
        operation_id='createItem',
        summary='Create an item',
        default_status=http.HTTPStatus.CREATED,
        tags=['items'],
    )
    @api.add_error_response(http.HTTPStatus.CONFLICT)
    async def post(
        self,
        body: typing.Annotated[
            CreateItemRequest, api.Body(description='The item to create')
        ],
    ) -> Item:
        if body.name == 'conflict':
            self.set_status(http.HTTPStatus.CONFLICT)
            raise api.wrap_error(
                http.HTTPStatus.CONFLICT,
                ErrorModel(status=409, title='Conflict'),
            )
        return Item(id=42, name=body.name)


class ItemHandler(tornado.web.RequestHandler):
    @api.expose_operation(tags=['items'])
    async def delete(self, item_id: int) -> None:  # noqa: ARG002
        self.set_status(204)

    @api.expose_operation(tags=['items'])
    async def get(self, item_id: int) -> Item:
        return Item(id=item_id, name='example')


class LegalButStrangeHandler:
    def __init__(
        self,
        _application: tornado.web.Application,
        _request: tornado.httputil.HTTPServerRequest,
        **_kwargs: object,
    ) -> None:
        pass


class Application(handlers.OpenAPIApplication):
    def __init__(self, **settings: object) -> None:
        super().__init__(
            [
                tornado.web.url(r'/items/(.*)', ItemHandler),
                tornado.web.url(r'/openapi.json', handlers.OpenAPISpecHandler),
                # We only care about URL-based routing so the following case
                # is legal and will be ignored by the library.
                tornado.routing.Rule(
                    tornado.routing.HostMatches('example.com'),
                    tornado.web.RequestHandler,
                ),
                # This is legal but strange since the handler is not a subclass
                # of RequestHandler. It is included here to ensure that the
                # library doesn't explode when encountering such a handler.
                tornado.web.url(r'/strange', LegalButStrangeHandler),
            ],
            **settings,
        )

        self.add_global_error(
            http.HTTPStatus.INTERNAL_SERVER_ERROR, ErrorModel
        )
        self.register_error_model(http.HTTPStatus.CONFLICT, ErrorModel)

        # The following tests adding a handler explicitly *after* setting
        # up an error model. Note that the @api.add_error_response decorator
        # does not mention the model. The openapi processing remembers that
        # CONFLICT is registered and stitches the information together.
        self.add_handlers(
            r'.*',
            [tornado.web.url(r'/items', CreateItemHandler, name='createItem')],
        )


class TestOpenAPIApplication(tests.AsyncTestCase[Application]):
    @staticmethod
    def create_app() -> Application:
        return Application()

    async def test_item_creation(self) -> None:
        rsp = await self.client.fetch(
            self.url('/items'),
            method='POST',
            body=json.dumps({'name': 'test'}),
            headers={'content-type': 'application/json'},
        )
        self.assertEqual(rsp.code, http.HTTPStatus.CREATED)

        data = json.loads(rsp.body)
        created = Item.model_validate(data)
        self.assertEqual(created.id, 42)
        self.assertEqual(created.name, 'test')

    async def test_openapi_json(self) -> None:
        rsp = await self.client.fetch(self.url('/openapi.json'))
        self.assertEqual(rsp.code, 200)

        data = json.loads(rsp.body)
        self.assertEqual(data, self.app.openapi_doc.render())

        self.assertEqual(
            data['paths']['/items']['post']['operationId'], 'createItem'
        )
        self.assertEqual(
            data['paths']['/items']['post']['summary'], 'Create an item'
        )
        self.assertEqual(data['paths']['/items']['post']['tags'], ['items'])
        self.assertEqual(
            data['paths']['/items']['post']['requestBody']['description'],
            'The item to create',
        )
        self.assertIn(
            str(http.HTTPStatus.CREATED),
            data['paths']['/items']['post']['responses'],
        )
        self.assertIn(
            str(http.HTTPStatus.CONFLICT),
            data['paths']['/items']['post']['responses'],
        )
        response = data['paths']['/items']['post']['responses']['409']
        self.assertEqual(
            response['content']['application/json']['schema']['$ref'],
            '#/components/schemas/ErrorModel',
        )
        self.assertIn('ErrorModel', data['components']['schemas'])

        for path, path_info in data['paths'].items():
            for method, operation in path_info.items():
                if operation not in ('delete', 'get', 'post', 'patch', 'put'):
                    continue

                self.assertIn(
                    'responses',
                    operation,
                    f'Missing responses for {path} {method}',
                )
                self.assertIn(
                    '500',
                    operation['responses'],
                    f'Missing 500 response for {path} {method}',
                )

    async def test_openapi_unnamed_parameters(self) -> None:
        data = self.app.openapi_doc.render()
        patn = re.compile(r'/items/{(?P<param>.*)}')
        found_param = False
        for path in data['paths']:  # type: ignore[attr-defined]
            if match := patn.match(path):
                param_names = [
                    param['name']
                    for param in data['paths'][path]['parameters']  # type: ignore[index]
                ]
                self.assertIn(match['param'], param_names)
                found_param = True
        self.assertTrue(found_param)

    async def test_calling_with_invalid_body(self) -> None:
        rsp = await self.client.fetch(
            self.url('/items'),
            method='POST',
            body=json.dumps({'invalid': 'body'}),
            headers={'content-type': 'application/json'},
            raise_error=False,
        )
        self.assertEqual(rsp.code, 422)

    async def test_path_parameter_processing(self) -> None:
        rsp = await self.client.fetch(self.url('/items/123'))
        self.assertEqual(rsp.code, 200)
        data = json.loads(rsp.body)
        self.assertEqual(data['id'], 123)

        rsp = await self.client.fetch(self.url('/items/123'), method='DELETE')
        self.assertEqual(rsp.code, 204)

        rsp = await self.client.fetch(
            self.url('/items/not-a-number'), raise_error=False
        )
        self.assertEqual(rsp.code, 400)

    async def test_find_existing_rule_by_name(self) -> None:
        rule = self.app.find_rule_by_name('createItem')
        self.assertIsNotNone(rule)
        self.assertEqual(rule.name, 'createItem')
        self.assertIs(rule.target, CreateItemHandler)

    async def test_find_non_existing_rule_by_name(self) -> None:
        with self.assertRaises(errors.RuleNotFoundError) as context:
            self.app.find_rule_by_name('non_existing_handler')
        self.assertEqual(context.exception.rule_name, 'non_existing_handler')

    async def test_tag_operation(self) -> None:
        self.app.openapi_doc.add_tag('tag1')
        self.app.openapi_doc.add_tag('tag2')
        self.app.tag_operation('createItem', 'POST', 'tag1', 'tag2')
        operation = self.app.openapi_doc.get_operation(
            self.app.find_rule_by_name('createItem'), 'POST'
        )
        self.assertIn('tag1', operation.tags)
        self.assertIn('tag2', operation.tags)

    async def test_tag_operation_with_non_existing_tag(self) -> None:
        with self.assertRaises(errors.TagNotFoundError):
            self.app.tag_operation('createItem', 'POST', 'non_existing_tag')

    async def test_tag_operation_with_new_tag(self) -> None:
        new_tag = models.Tag(name='new_tag', description='A new tag')
        self.app.tag_operation('createItem', 'POST', new_tag)
        operation = self.app.openapi_doc.get_operation(
            self.app.find_rule_by_name('createItem'), 'POST'
        )
        self.assertIn('new_tag', operation.tags)

    async def test_tag_operation_with_combination_of_tags(self) -> None:
        self.app.openapi_doc.add_tag('tag1')
        new_tag = models.Tag(name='new_tag', description='A new tag')
        self.app.tag_operation('createItem', 'POST', 'tag1', new_tag)
        operation = self.app.openapi_doc.get_operation(
            self.app.find_rule_by_name('createItem'), 'POST'
        )
        self.assertIn('tag1', operation.tags)
        self.assertIn('new_tag', operation.tags)

    async def test_duplicate_tag(self) -> None:
        self.app.openapi_doc.add_tag('tag1')
        with self.assertRaises(errors.DuplicateTagError):
            self.app.openapi_doc.add_tag('tag1')


class TestOpenAPIDocHandler(tests.AsyncTestCase[tornado.web.Application]):
    @staticmethod
    def create_app() -> tornado.web.Application:
        return tornado.web.Application(
            [
                tornado.web.url(
                    r'/openapi.json',
                    handlers.OpenAPISpecHandler,
                    name='spec_handler',
                ),
                tornado.web.url(
                    r'/docs',
                    handlers.OpenAPIDocHandler,
                    {'spec_handler_name': 'spec_handler'},
                ),
            ]
        )

    async def test_get_openapi_doc(self) -> None:
        rsp = await self.client.fetch(self.url('/docs'))
        self.assertEqual(rsp.code, 200)
        self.assertEqual(rsp.headers['content-type'], 'text/html')
        doc = importlib.resources.files('pydantictornado') / 'openapi.html'
        template = tornado.template.Template(
            doc.read_text(encoding='utf-8'), compress_whitespace=True
        )
        content = template.generate(
            url_for=lambda _: '/openapi.json', spec_handler_name='spec_handler'
        )
        self.assertEqual(rsp.body, content)

    async def test_cache_control_header(self) -> None:
        rsp = await self.client.fetch(self.url('/docs'))
        self.assertEqual(rsp.code, 200)
        self.assertEqual(rsp.headers['Cache-Control'], 'public, max-age=3600')


class TestErrorHandling(tests.AsyncTestCase[Application]):
    @staticmethod
    def create_app() -> Application:
        return Application()

    async def test_structured_error_handler(self) -> None:
        rsp = await self.client.fetch(
            self.url('/items'),
            method='POST',
            body=json.dumps({'name': 'conflict'}),
            headers={'content-type': 'application/json'},
            raise_error=False,
        )
        self.assertEqual(rsp.code, http.HTTPStatus.CONFLICT)
        self.assertEqual(rsp.headers['content-type'], 'application/json')
        error = ErrorModel.model_validate_json(rsp.body)
        self.assertEqual(error.status, 409)
        self.assertEqual(error.title, 'Conflict')

    def test_body_formatting(self) -> None:
        expected = ErrorModel(status=404, title='Not Found')
        content_type, body = self.app.format_body(
            unittest.mock.Mock(),
            expected.model_dump(mode='python'),
        )
        self.assertEqual(content_type, 'application/json')
        self.assertEqual(ErrorModel.model_validate_json(body), expected)

        content_type, body = self.app.format_body(unittest.mock.Mock(), body)
        self.assertEqual(content_type, 'application/json')
        self.assertEqual(
            ErrorModel.model_validate_json(body),
            expected,
            'body should not be serialized again',
        )

        result = self.app.format_body(unittest.mock.Mock(), None)
        self.assertEqual(content_type, 'application/json')
        self.assertIsNone(result)

    def test_simplest_error_formatting(self) -> None:
        result = self.app.format_error(unittest.mock.Mock(), 404)
        content_type, raw_body = self.unwrap(result, tuple[str, bytes])
        self.assertEqual(content_type, 'application/json')
        body = json.loads(raw_body.decode('utf-8'))
        self.assertEqual(body['status'], 404)
        self.assertEqual(
            body['title'], http.HTTPStatus.NOT_FOUND.phrase.title()
        )
        self.assertNotIn('detail', body)

    def test_error_formatting_http_error(self) -> None:
        error = tornado.web.HTTPError(404)
        result = self.app.format_error(
            unittest.mock.Mock(), 404, exc_info=(type(error), error, None)
        )
        content_type, raw_body = self.unwrap(result, tuple[str, bytes])
        self.assertEqual(content_type, 'application/json')
        body = json.loads(raw_body.decode('utf-8'))
        self.assertEqual(body['status'], 404)
        self.assertEqual(
            body['title'], http.HTTPStatus.NOT_FOUND.phrase.title()
        )
        self.assertEqual(body['detail'], str(error))

    def test_error_formatting_http_error_with_explicit_reason(self) -> None:
        error = tornado.web.HTTPError(404, reason='Item not found')
        result = self.app.format_error(
            unittest.mock.Mock(), 404, exc_info=(type(error), error, None)
        )
        content_type, raw_body = self.unwrap(result, tuple[str, bytes])
        self.assertEqual(content_type, 'application/json')
        body = json.loads(raw_body.decode('utf-8'))
        self.assertEqual(body['status'], 404)
        self.assertEqual(body['title'], 'Item not found')
        self.assertEqual(body['detail'], str(error))

    def test_creating_invalid_structured_error(self) -> None:
        with self.assertRaises(TypeError):
            api.StructuredError(404, None)  # type: ignore[arg-type]

    async def test_validation_errors(self) -> None:
        bad_body = {'invalid': 'body'}
        rsp = await self.client.fetch(
            self.url('/items'),
            method='POST',
            body=json.dumps(bad_body).encode('utf-8'),
            headers={'content-type': 'application/json'},
            raise_error=False,
        )
        self.assertEqual(rsp.code, 422)
        self.assertEqual(rsp.headers['content-type'], 'application/json')
        with self.assertRaises(pydantic.ValidationError) as cm:
            CreateItemRequest.model_validate(bad_body)
        formatted = cm.exception.errors(
            include_url=False, include_input=False, include_context=False
        )
        rsp_body = json.loads(rsp.body.decode('utf-8'))
        self.assertEqual(rsp_body['detail'], formatted[0]['msg'])

    async def test_strange_validation_error(self) -> None:
        failure = pydantic.ValidationError.from_exception_data(
            'Injected failure', []
        )
        with unittest.mock.patch.object(
            CreateItemRequest, 'model_validate_json'
        ) as model_validate:
            model_validate.side_effect = failure
            rsp = await self.client.fetch(
                self.url('/items'),
                method='POST',
                body=json.dumps({'invalid': 'body'}).encode('utf-8'),
                headers={'content-type': 'application/json'},
                raise_error=False,
            )
            self.assertEqual(rsp.code, 422)
            self.assertEqual(rsp.headers['content-type'], 'application/json')

        rsp_body = json.loads(rsp.body.decode('utf-8'))
        self.assertEqual(rsp_body['detail'], failure.title)
