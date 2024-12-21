import http
import importlib.resources
import json
import re
import typing
import unittest.mock

import pydantic
import tornado.web

import tests
from pydantictornado import api, handlers, openapi


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


class CreateItemHandler(tornado.web.RequestHandler):
    @handlers.decorate(
        operation_id='createItem',
        summary='Create an item',
        default_status=http.HTTPStatus.CREATED,
        tags=['items'],
    )
    async def post(
        self,
        body: typing.Annotated[
            CreateItemRequest, api.Body(description='The item to create')
        ],
    ) -> Item:
        return Item(id=42, name=body.name)


class ItemHandler(tornado.web.RequestHandler):
    @handlers.decorate(tags=['items'])
    async def delete(self, item_id: int) -> None:  # noqa: ARG002
        self.set_status(204)

    @handlers.decorate(tags=['items'])
    async def get(self, item_id: int) -> Item:
        return Item(id=item_id, name='example')


class Application(handlers.OpenAPIApplication):
    def __init__(self, **settings: object) -> None:
        super().__init__(
            [
                tornado.web.url(r'/items', CreateItemHandler),
                tornado.web.url(r'/items/(.*)', ItemHandler),
                tornado.web.url(r'/openapi.json', handlers.OpenAPISpecHandler),
            ],
            **settings,
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


class TestOpenAPIDocHandler(tests.AsyncTestCase[tornado.web.Application]):
    @staticmethod
    def create_app() -> tornado.web.Application:
        return tornado.web.Application(
            [
                tornado.web.url(r'/docs', handlers.OpenAPIDocHandler),
            ]
        )

    async def test_get_openapi_doc(self) -> None:
        rsp = await self.client.fetch(self.url('/docs'))
        self.assertEqual(rsp.code, 200)
        self.assertEqual(rsp.headers['content-type'], 'text/html')
        doc = importlib.resources.files('pydantictornado') / 'openapi.html'
        content = doc.read_text(encoding='utf-8')
        self.assertEqual(rsp.body.decode(), content)

    async def test_cache_control_header(self) -> None:
        rsp = await self.client.fetch(self.url('/docs'))
        self.assertEqual(rsp.code, 200)
        self.assertEqual(rsp.headers['Cache-Control'], 'public, max-age=3600')
