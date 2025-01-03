import socket
import typing
import unittest
import urllib.parse

from tornado import httpclient, httpserver, testing, web

ApplicationType = typing.TypeVar('ApplicationType', bound=web.Application)


class AsyncTestCase(
    unittest.IsolatedAsyncioTestCase, typing.Generic[ApplicationType]
):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = self.create_app()
        self.server, sock = self.create_server()
        self.client = httpclient.AsyncHTTPClient(force_instance=True)
        self.server_address, self.server_port = sock.getsockname()

    async def asyncTearDown(self) -> None:
        self.server.stop()
        await self.server.close_all_connections()
        await super().asyncTearDown()

    @staticmethod
    def create_app() -> ApplicationType:
        raise NotImplementedError()

    def create_server(self) -> tuple[httpserver.HTTPServer, socket.SocketType]:
        server = httpserver.HTTPServer(self.app)
        server_sock, _ = testing.bind_unused_port(
            reuse_port=True, address='localhost'
        )
        server.add_sockets([server_sock])
        return server, server_sock

    def url(self, path: str) -> str:
        return urllib.parse.urljoin(
            f'http://{self.server_address}:{self.server_port}/', path
        )

    def unwrap[T](self, obj: object, _cast_to: type[T]) -> T:
        self.assertIsNotNone(obj)
        return typing.cast(T, obj)
