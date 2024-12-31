"""Canonical REST API example

https://www.infoq.com/articles/webber-rest-workflow/

"""

import asyncio
import contextlib
import enum
import http.client
import logging
import types
import typing
from collections import abc

import pydantic
from tornado import httputil, routing, web

from pydantictornado import api, handlers


class ErrorResponse(pydantic.BaseModel):
    status: int
    title: str


class NotFoundErrorResponse(ErrorResponse):
    status: int = 404
    title: str = 'Not Found'


class DrinkType(enum.StrEnum):
    """Type of drinks that you can order"""

    AMERICANO = 'americano'
    CAPPUCCINO = 'cappuccino'
    ESPRESSO = 'espresso'
    FLAT_WHITE = 'flat white'
    LATTE = 'latte'


class DrinkSize(enum.StrEnum):
    SOLO = 'solo'
    DEMI = 'demi'
    DOPPIO = 'doppio'
    GRANDE = 'grande'
    QUAD = 'quad'
    SHORT = 'short'
    TALL = 'tall'
    TRIPLE = 'triple'
    VENTI = 'venti'


class Item(pydantic.BaseModel):
    drink: DrinkType
    size: DrinkSize


class Order(pydantic.RootModel[list[Item]]):
    def __len__(self) -> int:
        return len(self.root)

    def __iter__(self) -> abc.Iterator[Item]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, item: int) -> Item:
        return self.root[item]


class ActiveOrder(pydantic.BaseModel):
    order_id: int
    items: list[Item] = pydantic.Field(default_factory=list)
    price: float = 0.0


MENU: dict[DrinkType, dict[DrinkSize, float]] = {
    DrinkType.AMERICANO: {
        DrinkSize.SHORT: 3.65,
        DrinkSize.TALL: 3.75,
        DrinkSize.GRANDE: 3.95,
        DrinkSize.VENTI: 4.25,
    },
    DrinkType.CAPPUCCINO: {
        DrinkSize.SHORT: 4.55,
        DrinkSize.TALL: 4.65,
        DrinkSize.GRANDE: 5.25,
        DrinkSize.VENTI: 5.65,
    },
    DrinkType.ESPRESSO: {
        DrinkSize.SOLO: 2.75,
        DrinkSize.DOPPIO: 2.95,
        DrinkSize.TRIPLE: 3.45,
        DrinkSize.QUAD: 3.85,
    },
    DrinkType.LATTE: {
        DrinkSize.SHORT: 4.55,
        DrinkSize.TALL: 4.65,
        DrinkSize.GRANDE: 5.25,
        DrinkSize.VENTI: 5.65,
    },
}


class ErrorKwargs(typing.TypedDict, total=False):
    exc_info: typing.NotRequired[
        tuple[
            type,
            BaseException,
            types.TracebackType | None,
        ]
    ]
    reason: typing.NotRequired[str]


class OrderManager:
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__package__).getChild('OrderManager')
        self.orders: dict[int, ActiveOrder] = {}
        self._order_id = 1

    def create_order(self, order: Order) -> ActiveOrder:
        order_id, self._order_id = self._order_id, self._order_id + 1
        active_order = ActiveOrder(order_id=order_id)
        for item in order:
            try:
                price = self.menu[item.drink][item.size]
            except KeyError:
                raise web.HTTPError(
                    400, reason='Item not found in menu'
                ) from None
            active_order.items.append(item)
            active_order.price += price
        self.orders[order_id] = active_order
        self.logger.info(
            'created order ID %r, current cost %.2f',
            order_id,
            active_order.price,
        )
        return active_order

    @property
    def menu(self) -> abc.Mapping[DrinkType, abc.Mapping[DrinkSize, float]]:
        return MENU


class Application(handlers.OpenAPIApplication, OrderManager, web.Application):
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
        self.register_error_model(404, NotFoundErrorResponse)


class RequestHandler(web.RequestHandler):
    application: Application

    def __init__(
        self,
        application: web.Application,
        request: httputil.HTTPServerRequest,
        **kwargs: object,
    ) -> None:
        super().__init__(application, request, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def write_error(  # type: ignore[override]
        self, status_code: int, **kwargs: typing.Unpack[ErrorKwargs]
    ) -> None:
        exc_info = kwargs.get('exc_info')
        reason = kwargs.get('reason')
        if not reason and exc_info:
            exc_value = exc_info[1]
            if isinstance(exc_value, web.HTTPError):
                reason = exc_value.reason
        if not reason:
            reason = http.client.responses.get(status_code, 'Unknown')

        body = {'status': status_code, 'title': reason}
        self.set_header('Content-Type', 'application/problem+json')
        self.write(body)


class CreateOrderHandler(RequestHandler):
    @api.expose_operation(default_status=201, summary='Create a new order')
    async def post(
        self,
        body: typing.Annotated[Order, api.Body(description='Order details')],
    ) -> ActiveOrder:
        self.logger.info('doin the thing with %s', body)
        return self.application.create_order(body)


class OrderHandler(RequestHandler):
    @api.expose_operation(summary='Retrieve order details')
    @api.add_error_response(404, description='Order not found')
    async def get(self, order_id: int) -> ActiveOrder:
        self.logger.info('fetching %r', order_id)
        try:
            return self.application.orders[order_id]
        except KeyError:
            raise web.HTTPError(404, reason='Order not found') from None


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format='%(levelname)-15s %(name)s: %(message)s'
    )
    logger = logging.getLogger(__name__)
    app = Application(autoreload=True, debug=True, serve_traceback=False)
    app.listen(8000)
    logger.info('Listening on http://localhost:8000/')
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
