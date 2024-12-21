"""Canonical REST API example

https://www.infoq.com/articles/webber-rest-workflow/

"""

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
            routing.URLSpec('/docs', handlers.OpenAPIDocHandler),
            routing.URLSpec(
                '/orders', CreateOrderHandler, name='create_order'
            ),
            routing.URLSpec(
                '/orders/(?P<order_id>.*)', OrderHandler, name='order_handler'
            ),
            routing.URLSpec('/openapi.json', handlers.OpenAPISpecHandler),
        ]
        super().__init__(routes, **kwargs)

        order_management = self.openapi_doc.add_tag(
            'Order Management', 'Operations related to order management'
        )
        self.tag_operation('create_order', 'POST', order_management)
        self.tag_operation('order_handler', 'GET', order_management)


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
    @handlers.decorate(default_status=201)
    async def post(
        self,
        body: typing.Annotated[Order, api.Body(description='Order details')],
    ) -> Order:
        self.logger.info('doin the thing with %s', body)
        return body


class OrderHandler(RequestHandler):
    @handlers.decorate
    async def get(self, order_id: int) -> Order:
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
