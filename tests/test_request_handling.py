import datetime
import http
import ipaddress
import json
import typing
import unittest.mock
import uuid

import yarl
from pydantictornado import request_handling
from tornado import http1connection, httputil, testing, web
from tornado.web import Application


def create_request(**kwargs: object) -> httputil.HTTPServerRequest:
    kwargs.setdefault(
        'connection', unittest.mock.Mock(spec=http1connection.HTTP1Connection)
    )
    return httputil.HTTPServerRequest(**kwargs)  # type: ignore[arg-type]


class RequestTracker:
    def __init__(self) -> None:
        self.impl = unittest.mock.AsyncMock()

    def assert_awaited_once_with(
        self, *args: object, **kwargs: object
    ) -> None:
        self.impl.assert_awaited_once_with(*args, **kwargs)

    async def injects_application(self, app: web.Application) -> None:
        await self.impl(app=app)

    async def injects_handler(self, handler: web.RequestHandler) -> None:
        await self.impl(handler=handler)

    async def injects_request(self, req: httputil.HTTPServerRequest) -> None:
        await self.impl(req=req)

    async def injects_path_kwarg(self, int_kwarg: int) -> None:
        await self.impl(int_kwarg=int_kwarg)


class RequestHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.application = web.Application()

    def create_request_handler(
        self,
        *,
        request_kwargs: dict[str, typing.Any] | None = None,
        **kwargs: request_handling.RequestMethod,
    ) -> request_handling.RequestHandler:
        # NB ... HTTPServerRequest uses conn.set_close_callback which
        # does not exist on httputil.HTTPConnection :/
        request_kwargs = {} if request_kwargs is None else request_kwargs
        request_kwargs.setdefault('method', 'GET')
        request = create_request(**request_kwargs)
        return request_handling.RequestHandler(
            self.application, request, **kwargs
        )

    def test_that_supported_methods_are_updated(self) -> None:
        handler = self.create_request_handler()
        self.assertEqual((), handler.SUPPORTED_METHODS)

        handler = self.create_request_handler(get=unittest.mock.AsyncMock())
        self.assertEqual(('GET',), handler.SUPPORTED_METHODS)

        kwargs = {
            method.lower(): unittest.mock.AsyncMock()
            for method in web.RequestHandler.SUPPORTED_METHODS
        }
        handler = self.create_request_handler(**kwargs)
        self.assertEqual(
            sorted(web.RequestHandler.SUPPORTED_METHODS),
            sorted(handler.SUPPORTED_METHODS),
        )

    def test_that_additional_kwargs_are_rejected(self) -> None:
        with self.assertRaises(TypeError) as context:
            self.create_request_handler(whatever=unittest.mock.AsyncMock())
        self.assertIn('whatever', str(context.exception))

    async def test_that_unimplemented_method_fails(self) -> None:
        for http_method in set(web.RequestHandler.SUPPORTED_METHODS) - {'GET'}:
            handler = self.create_request_handler(
                get=unittest.mock.AsyncMock(),
                request_kwargs={'method': http_method},
            )
            method = getattr(handler, http_method.lower(), None)
            self.assertIsNotNone(method)
            with self.assertRaises(web.HTTPError) as context:
                await method()  # type: ignore[misc] # mypy should know better
            self.assertEqual(
                http.HTTPStatus.METHOD_NOT_ALLOWED,
                context.exception.status_code,
            )

    async def test_that_handler_calls_implementation_functions(self) -> None:
        func = unittest.mock.AsyncMock()
        func.return_value = None
        handler = self.create_request_handler(get=func)
        response = await handler.get()
        func.assert_awaited_once()
        self.assertIsNone(response)

    async def test_that_non_coroutine_implementation_fails(self) -> None:
        handler = self.create_request_handler(get=print)  # type: ignore[arg-type]
        with self.assertRaises(web.HTTPError) as context:
            await handler.get()
        self.assertEqual(
            http.HTTPStatus.INTERNAL_SERVER_ERROR,
            context.exception.status_code,
        )


class ParameterProcessingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.application = web.Application()
        self.tracker = RequestTracker()
        self.request = create_request(method='GET')

    async def test_that_request_is_injected_on_demand(self) -> None:
        handler = request_handling.RequestHandler(
            self.application, self.request, get=self.tracker.injects_request
        )
        await handler.get()
        self.tracker.assert_awaited_once_with(req=self.request)

    async def test_that_application_is_injected_on_demand(self) -> None:
        handler = request_handling.RequestHandler(
            self.application,
            self.request,
            get=self.tracker.injects_application,
        )
        await handler.get()
        self.tracker.assert_awaited_once_with(app=self.application)

    async def test_that_handler_is_injected_on_demand(self) -> None:
        handler = request_handling.RequestHandler(
            self.application, self.request, get=self.tracker.injects_handler
        )
        await handler.get()
        self.tracker.assert_awaited_once_with(handler=handler)

    async def test_that_path_kwarg_is_injected_on_demand(self) -> None:
        handler = request_handling.RequestHandler(
            self.application,
            self.request,
            get=self.tracker.injects_path_kwarg,
            path_types={'int_kwarg': lambda i: int(i, 10)},
        )
        await handler.get(int_kwarg='42')
        self.tracker.assert_awaited_once_with(int_kwarg=42)

    async def test_that_unspecified_path_kwargs_are_injected(self) -> None:
        handler = request_handling.RequestHandler(
            self.application,
            self.request,
            get=self.tracker.injects_path_kwarg,
        )
        await handler.get(int_kwarg='42')
        self.tracker.assert_awaited_once_with(int_kwarg='42')


class ResponseHandlingTests(testing.AsyncHTTPTestCase):
    application: web.Application  # created in setUp()

    def setUp(self) -> None:
        self.application = None  # type: ignore[assignment]
        super().setUp()

    def get_app(self) -> Application:
        self.application = web.Application([])
        return self.application

    def register_handler_function(
        self,
        pattern: str,
        method: str,
        # func: typing.Callable[
        #     [], typing.Awaitable[request_handling.ResponseType] ],
        func: request_handling.RequestMethod,
    ) -> None:
        self.application.add_handlers(
            r'.*',
            [
                web.url(
                    pattern,
                    request_handling.RequestHandler,
                    kwargs={method.lower(): func},
                )
            ],
        )

    def test_simple_responses(self) -> None:
        expected: dict[str, bool | float | int | str | None] = {
            'string': 'hello world',
            'int': 42,
            'bool': True,
            'float': 22.0 / 7.0,
            'null': None,
        }

        async def impl(**_kwargs: object) -> request_handling.ResponseType:
            return expected

        self.register_handler_function(r'/', 'GET', impl)
        response = self.fetch('/', headers={'accept': 'application/json'})
        self.assertEqual(200, response.code)
        self.assertTrue(response.body, 'response body should not be empty')
        self.assertEqual(
            'application/json',
            response.headers['content-type'].partition(';')[0].strip(),
        )
        body = json.loads(response.body.decode('utf-8'))
        self.assertDictEqual(expected, body)

    def test_returning_null(self) -> None:
        async def impl() -> request_handling.ReturnsNone:
            return None

        self.register_handler_function(r'/', 'GET', impl)
        response = self.fetch('/', headers={'accept': 'application/json'})
        self.assertEqual(200, response.code)
        self.assertEqual(
            'application/json',
            response.headers['content-type'].partition(';')[0].strip(),
        )
        self.assertEqual(b'null', response.body)

    def test_library_types(self) -> None:
        uid = uuid.uuid4()
        now = datetime.datetime.now(datetime.UTC)
        value: request_handling.ResponseType = [
            now.date(),
            now,
            now.time(),
            datetime.timedelta(
                hours=1, minutes=2, seconds=3, microseconds=456789
            ),
            ipaddress.IPv4Address('127.0.0.1'),
            ipaddress.IPv6Address('::1'),
            uid,
            yarl.URL('https://example.com'),
        ]
        expected = [
            now.date().isoformat(),
            now.isoformat(),
            now.time().isoformat(),
            'PT1H2M3.456789S',
            '127.0.0.1',
            '::1',
            str(uid).lower(),
            'https://example.com',
        ]

        async def impl(**_kwargs: object) -> request_handling.ResponseType:
            return value

        self.register_handler_function(r'/', 'GET', impl)
        response = self.fetch('/', headers={'accept': 'application/json'})
        self.assertEqual(200, response.code)
        self.assertTrue(response.body, 'response body should not be empty')
        self.assertEqual(
            'application/json',
            response.headers['content-type'].partition(';')[0].strip(),
        )
        body = json.loads(response.body.decode('utf-8'))
        self.assertEqual(expected, body)
