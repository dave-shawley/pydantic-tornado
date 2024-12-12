import typing
import unittest

import pydantic
from tornado import web

from pydantictornado import api, errors, handlers, models


class Model(pydantic.BaseModel):
    name: str


class AnotherModel(pydantic.BaseModel):
    id: int


class DecorateTests(unittest.IsolatedAsyncioTestCase):
    def extract_marker(self, func: object) -> models.OpenAPIMethodMarker:
        try:
            return models.OpenAPIMethodMarker.extract(func)
        except errors.MarkerNotFoundError:
            self.fail(f'marker not found on {func}')
        except TypeError as error:
            self.fail(str(error))

    def test_invalid_calls(self) -> None:
        class Handler(web.RequestHandler):
            async def get(self) -> None:
                return None

        with self.assertRaises(TypeError):
            handlers.decorate(Handler.get, 1)  # type: ignore[call-overload]
        with self.assertRaises(TypeError):
            handlers.decorate('param')
        with self.assertRaises(TypeError):
            handlers.decorate(print)  # type: ignore[arg-type]

    def test_parameterless_handler(self) -> None:
        class Handler(web.RequestHandler):
            @handlers.decorate
            async def post(self) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertIs(marker, models.OpenAPIMethodMarker.EMPTY)

    def test_body_parameter_detection(self) -> None:
        class Handler(web.RequestHandler):
            @handlers.decorate
            async def post(
                self, *, body: typing.Annotated[Model, api.Body]
            ) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertEqual(marker.body_param_name, 'body')
        self.assertEqual(marker.body_param_type, Model)

    def test_that_extra_annotations_are_ignored(self) -> None:
        class Handler(web.RequestHandler):
            @handlers.decorate
            async def post(
                self, *, body: typing.Annotated[Model, api.Body, 'ignored']
            ) -> None:
                pass

        marker = self.extract_marker(Handler.post)
        self.assertEqual(marker.body_param_name, 'body')
        self.assertEqual(marker.body_param_type, Model)

    def test_unsupported_union_parameter(self) -> None:
        with self.assertRaises(errors.UnsupportedAnnotationError):

            class Handler(web.RequestHandler):
                @handlers.decorate
                async def post(
                    self,
                    *,
                    body: typing.Annotated[Model | AnotherModel, api.Body],
                ) -> None:
                    pass

    def test_unannotated_body_param(self) -> None:
        with self.assertRaises(errors.UnsupportedAnnotationError):

            class Handler(web.RequestHandler):
                @handlers.decorate
                async def post(self, *, body) -> None:  # type: ignore[no-untyped-def] # noqa: ANN001
                    pass

    def test_explicit_parameters(self) -> None:
        class Handler(web.RequestHandler):
            @handlers.decorate(operation_id='create.something')
            async def post(
                self, body: typing.Annotated[Model, api.Body], parent_id: int
            ) -> None:
                pass

        marker = models.OpenAPIMethodMarker.extract(Handler.post)
        self.assertEqual(
            marker.extra,
            {
                'operation_id': 'create.something',
            },
        )

    def test_unannotated_handler(self) -> None:
        class Handler(web.RequestHandler):
            @handlers.decorate
            async def post(self):  # type: ignore[no-untyped-def]  # noqa: ANN202
                pass

        marker = models.OpenAPIMethodMarker.extract(Handler.post)
        self.assertIsNone(marker.response_type)
