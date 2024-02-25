import collections
import inspect
import logging
import typing

import tornado.httputil
from tornado import web

from pydantictornado import util

PathCoercion = typing.Callable[[str], typing.Any]


class RequestMethod(typing.Protocol):
    async def __call__(self, /, **kwargs: object) -> None:
        ...


def identity_transform_factory() -> PathCoercion:
    return lambda s: s


class RequestHandler(web.RequestHandler):
    implementations: dict[str, RequestMethod]
    logger: logging.Logger
    __initialization_failure: Exception | None
    __path_coercions: dict[str, PathCoercion]

    # The following annotations help out type checkers. The
    # methods are bound in `initialize()`.
    delete: RequestMethod
    get: RequestMethod
    head: RequestMethod
    options: RequestMethod
    patch: RequestMethod
    post: RequestMethod
    put: RequestMethod

    def initialize(
        self,
        *,
        path_types: dict[str, PathCoercion] | None = None,
        **kwargs: object,
    ) -> None:
        self.__initialization_failure = None
        self.logger = util.get_logger_for(self)
        self.implementations = {}
        self.__path_coercions = collections.defaultdict(
            identity_transform_factory
        )
        if path_types:
            self.__path_coercions.update(path_types)

        for http_method in self.SUPPORTED_METHODS:
            key = http_method.lower()
            if func := typing.cast(RequestMethod, kwargs.pop(key, None)):
                if not inspect.iscoroutinefunction(func):
                    self.logger.critical(
                        'implementation method for %r is not a co-routine', key
                    )
                    self.__initialization_failure = web.HTTPError(500)
                self.implementations[http_method] = func
                setattr(self, key, self._handle_request)
        self.SUPPORTED_METHODS = tuple(self.implementations.keys())  # type: ignore[assignment]

        super().initialize(**kwargs)

    async def _handle_request(self, **path_kwargs: str) -> None:
        if self.__initialization_failure is not None:
            raise self.__initialization_failure
        if self.request.method is None:
            raise web.HTTPError(500)  # pragma: nocover -- should not happen!

        func = self.implementations[self.request.method]
        sig = inspect.signature(func, eval_str=True)

        kwargs = {
            name: self.__path_coercions[name](value)
            for name, value in path_kwargs.items()
        }

        for name, param in sig.parameters.items():
            if issubclass(
                param.annotation, tornado.httputil.HTTPServerRequest
            ):
                kwargs[name] = self.request
            elif issubclass(param.annotation, tornado.web.Application):
                kwargs[name] = self.application
            elif issubclass(param.annotation, tornado.web.RequestHandler):
                kwargs[name] = self

        await func(**kwargs)
