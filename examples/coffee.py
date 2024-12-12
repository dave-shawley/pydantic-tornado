import asyncio
import enum
import logging
import typing
from contextlib import suppress

import pydantic
from tornado import httputil, routing, web

from pydantictornado import api, handlers


class Application(handlers.OpenAPIApplication, web.Application):
    def __init__(self, **kwargs: object) -> None:
        routes: list[routing.Rule] = [
            routing.URLSpec('/orders', CreateOrderHandler),
            routing.URLSpec('/orders/(?P<order_id>.*)', OrderHandler),
            routing.URLSpec('/openapi.json', handlers.OpenDocAPIHandler),
        ]
        super().__init__(routes, **kwargs)


class DrinkType(enum.StrEnum):
    LATTE = 'latte'


class DrinkSize(enum.StrEnum):
    DEMI = 'demi'
    SHORT = 'short'
    TALL = 'tall'
    GRANDE = 'grande'
    VENTI = 'venti'


class Order(pydantic.BaseModel):
    drink: DrinkType
    size: DrinkSize


class RequestHandler(web.RequestHandler):
    def __init__(
        self,
        application: web.Application,
        request: httputil.HTTPServerRequest,
        **kwargs: object,
    ) -> None:
        super().__init__(application, request, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)


class CreateOrderHandler(RequestHandler):
    @handlers.decorate
    async def post(
        self, body: typing.Annotated[Order, api.Body('foo')]
    ) -> Order:
        self.logger.info('doin the thing with %s', body)
        return body


class OrderHandler(RequestHandler):
    @handlers.decorate
    async def get(self, order_id: str) -> Order:
        self.logger.info('fetching %r', order_id)
        return Order(drink=DrinkType.LATTE, size=DrinkSize.TALL)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format='%(levelname)-15s %(name)s: %(message)s'
    )
    logger = logging.getLogger(__name__)
    app = Application(debug=True, autoreload=True)
    app.listen(8000)
    logger.info('Listening on http://localhost:8000/')
    with suppress(asyncio.CancelledError):
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
