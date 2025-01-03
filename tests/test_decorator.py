import typing
import unittest.mock

import pydantic
from tornado import web

from pydantictornado import api, errors

HandlerType = typing.TypeVar('HandlerType', bound=web.RequestHandler)


class Model(pydantic.BaseModel):
    name: str


class AnotherModel(pydantic.BaseModel):
    id: int


class DecorateTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def create_handler(
        handler_cls: type[HandlerType],
        *,
        body: pydantic.BaseModel | None = None,
    ) -> HandlerType:
        app = unittest.mock.Mock()
        app.ui_methods = {}
        request = unittest.mock.Mock()
        if body is not None:
            request.body = body.model_dump_json().encode()
        return handler_cls(app, request)

    def extract_marker(self, func: object) -> api.OpenAPIMethodInfo:
        try:
            return api.OpenAPIMethodInfo.extract(func)
        except errors.MarkerNotFoundError:
            self.fail(f'marker not found on {func}')
        except TypeError as error:
            self.fail(str(error))

    def test_invalid_calls(self) -> None:
        class Handler(web.RequestHandler):
            async def get(self) -> None:
                return None

        with self.assertRaises(TypeError):
            api.expose_operation(Handler.get, 1)  # type: ignore[call-overload]
        with self.assertRaises(TypeError):
            api.expose_operation('param')
        with self.assertRaises(TypeError):
            api.expose_operation(print)  # type: ignore[arg-type]

    def test_parameterless_handler(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def post(self) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertIs(marker, api.OpenAPIMethodInfo.EMPTY)

    async def test_path_parameters(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def get(self, _parent_id: int, name: str) -> Model:
                return Model(name=name)

        marker = self.extract_marker(Handler.get)
        self.assertIs(marker.parameters['_parent_id'].annotation, int)
        self.assertIs(marker.parameters['name'].annotation, str)

    def test_body_parameter_detection(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def post(
                self, *, body: typing.Annotated[Model, api.Body]
            ) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertEqual(marker.request_body.name, 'body')
        self.assertEqual(marker.request_body.type, Model)

    def test_that_extra_annotations_are_ignored(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def post(
                self, *, body: typing.Annotated[Model, api.Body, 'ignored']
            ) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertEqual(marker.request_body.name, 'body')
        self.assertEqual(marker.request_body.type, Model)

    def test_unsupported_body_parameter(self) -> None:
        with self.assertRaises(errors.UnsupportedAnnotationError):

            class Handler(web.RequestHandler):
                @api.expose_operation
                async def post(
                    self,
                    *,
                    body: typing.Annotated[str, api.Body],
                ) -> None:
                    pass

    def test_unsupported_union_parameter(self) -> None:
        with self.assertRaises(errors.UnsupportedAnnotationError):

            class Handler(web.RequestHandler):
                @api.expose_operation
                async def post(
                    self,
                    *,
                    body: typing.Annotated[Model | AnotherModel, api.Body],
                ) -> None:
                    pass

        with self.assertRaises(errors.UnsupportedAnnotationError):

            class AnotherHandler(web.RequestHandler):
                @api.expose_operation
                async def post(self, arg: int | str) -> None:
                    pass

    def test_unannotated_body_param(self) -> None:
        with self.assertRaises(errors.UnsupportedAnnotationError):

            class Handler(web.RequestHandler):
                @api.expose_operation
                async def post(self, *, body) -> None:  # type: ignore[no-untyped-def] # noqa: ANN001
                    pass

    def test_wildcard_parameters(self) -> None:
        with self.assertRaises(errors.UnsupportedParameterError):

            class Handler(web.RequestHandler):
                @api.expose_operation
                async def post(self, **kwargs: object) -> None:
                    pass

    def test_explicit_parameters(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation(operation_id='create.something')
            async def post(
                self, body: typing.Annotated[Model, api.Body], parent_id: int
            ) -> None:
                pass

        marker = api.OpenAPIMethodInfo.extract(Handler.post)
        self.assertEqual(
            marker.extra,
            {
                'operation_id': 'create.something',
            },
        )

    def test_unannotated_handler(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def post(self):  # type: ignore[no-untyped-def]  # noqa: ANN202
                pass

        marker = api.OpenAPIMethodInfo.extract(Handler.post)
        self.assertIsNone(marker.response_type)

    async def test_positional_and_keyword_args(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def put(  # noqa: PLR0913
                self,
                category: str,
                parent_id: int,
                /,
                item_id: int,
                *,
                overwrite: bool,
                content: typing.Annotated[Model, api.Body],
                cost: float = 0.0,
            ) -> None:
                self.write(
                    {
                        'category': type(category).__name__,
                        'content': type(content).__name__,
                        'cost': type(cost).__name__,
                        'parent_id': type(parent_id).__name__,
                        'item_id': type(item_id).__name__,
                        'overwrite': type(overwrite).__name__,
                    }
                )

        marker = self.extract_marker(Handler.put)
        self.assertEqual(marker.parameters['category'].annotation, str)
        self.assertEqual(marker.parameters['cost'].annotation, float)
        self.assertEqual(marker.parameters['parent_id'].annotation, int)
        self.assertEqual(marker.parameters['item_id'].annotation, int)
        self.assertEqual(marker.parameters['overwrite'].annotation, bool)

        handler = self.create_handler(Handler, body=Model(name='whatever'))
        handler.write = unittest.mock.Mock()  # type: ignore[method-assign]
        await handler.put('items', '1', '2', overwrite='yes', cost=1.23)  # type: ignore[misc]
        handler.write.assert_called_once_with(
            {
                'category': 'str',
                'content': 'Model',
                'cost': 'float',
                'item_id': 'int',
                'overwrite': 'bool',
                'parent_id': 'int',
            }
        )

        handler.write.reset_mock()
        await handler.put(  # item_id is kwarg or positional
            'items', '1', item_id='2', overwrite='yes', cost=1.23
        )  # type: ignore[misc]
        handler.write.assert_called_once_with(
            {
                'category': 'str',
                'content': 'Model',
                'cost': 'float',
                'item_id': 'int',
                'overwrite': 'bool',
                'parent_id': 'int',
            }
        )

    async def test_positional_body_parameter(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation
            async def post(
                self, body: typing.Annotated[Model, api.Body], item_id: int
            ) -> None:
                pass

        handler = self.create_handler(Handler, body=Model(name='whatever'))
        await handler.post('12')  # type: ignore[misc]

    async def test_default_status(self) -> None:
        class Handler(web.RequestHandler):
            @api.expose_operation(default_status=201)
            async def post(self) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertEqual(marker.extra['default_status'], 201)

        handler = self.create_handler(Handler)
        with unittest.mock.patch.object(handler, 'set_status') as set_status:
            await handler.post()  # type: ignore[misc]
            set_status.assert_called_once_with(201)
