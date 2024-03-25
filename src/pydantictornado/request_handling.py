import collections.abc
import datetime
import inspect
import ipaddress
import json
import logging
import typing
import uuid

import tornado.httputil
import yarl
from tornado import web

from pydantictornado import util

ReturnsNone = typing.Annotated[None, 'ReturnsNone']
"""Explicitly mark functions that return `None` as a value

This is used to distinguish between explicitly returning `None`
versus implicitly returning `None` with an empty return.

"""

PathCoercion = typing.Callable[[str], typing.Any]

ResponseType: typing.TypeAlias = (  # - mypy doesn't support type here
    bool
    | float
    | int
    | str
    | datetime.date
    | datetime.datetime
    | datetime.time
    | datetime.timedelta
    | ipaddress.IPv4Address
    | ipaddress.IPv6Address
    | uuid.UUID
    | yarl.URL
    | collections.abc.Mapping[str, 'ResponseType']
    | collections.abc.Sequence['ResponseType']
    | None
    | ReturnsNone
)

RequestMethod = typing.Callable[..., typing.Awaitable[ResponseType]]


def identity_transform_factory() -> PathCoercion:
    return lambda s: s


class RequestHandler(web.RequestHandler):
    implementations: dict[str, RequestMethod]
    logger: logging.Logger
    __initialization_failure: Exception | None
    __path_coercions: dict[str, PathCoercion]

    # The following annotations help out type checkers. The
    # methods are bound in `initialize()`.
    delete: RequestMethod  # type: ignore[assignment] # signature mismatch
    get: RequestMethod  # type: ignore[assignment] # signature mismatch
    head: RequestMethod  # type: ignore[assignment] # signature mismatch
    options: RequestMethod  # type: ignore[assignment] # signature mismatch
    patch: RequestMethod  # type: ignore[assignment] # signature mismatch
    post: RequestMethod  # type: ignore[assignment] # signature mismatch
    put: RequestMethod  # type: ignore[assignment] # signature mismatch

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
            func = kwargs.pop(key, util.UNSPECIFIED)
            if func is not util.UNSPECIFIED:
                if not util.is_coroutine_function(func):
                    self.logger.critical(
                        'implementation method for %r is not a co-routine', key
                    )
                    self.__initialization_failure = web.HTTPError(500)
                self.implementations[http_method] = typing.cast(
                    RequestMethod, func
                )
                setattr(self, key, self._handle_request)
        self.SUPPORTED_METHODS = tuple(self.implementations.keys())  # type: ignore[assignment]

        super().initialize(**kwargs)

    async def _handle_request(self, **path_kwargs: str) -> None:
        if self.__initialization_failure is not None:
            raise self.__initialization_failure
        if self.request.method is None:
            raise web.HTTPError(500)  # pragma: nocover -- should not happen!

        func = util.strip_annotation(self.implementations[self.request.method])
        sig = inspect.signature(func, eval_str=True)

        try:
            kwargs = {
                name: self.__path_coercions[name](value)
                for name, value in path_kwargs.items()
            }
        except ValueError:
            raise web.HTTPError(400) from None
        self.__handle_injections(sig.parameters, kwargs)

        result = await func(**kwargs)
        if result is not None or sig.return_annotation == ReturnsNone:
            self.send_response(result)

    def send_response(
        self, body: ResponseType | None, *_args: object, **_kwargs: object
    ) -> None:
        self.set_header('content-type', 'application/json; charset="UTF-8"')
        self.write(
            json.dumps(body, default=util.json_serialize_hook).encode('utf-8')
        )

    def __handle_injections(
        self,
        annotations: collections.abc.Mapping[str, inspect.Parameter],
        kwargs: dict[str, object],
    ) -> None:
        mapping = util.ClassMapping[object](
            {
                tornado.httputil.HTTPServerRequest: self.request,
                tornado.web.Application: self.application,
                tornado.web.RequestHandler: self,
            }
        )
        kwargs.update(
            {
                name: value
                for name, param in annotations.items()
                if (value := mapping.get(param.annotation, util.UNSPECIFIED))
            }
        )
