"""Canonical REST API example

This example is loosely based on the classic [How to GET a Cup of Coffee] by
Jim Webber. It demonstrates a simple REST API for ordering coffee drinks.

[How to GET a Cup of Coffee]: https://www.infoq.com/articles/webber-rest-workflow/

"""

import asyncio
import contextlib
import enum
import http
import logging
import typing
from collections import abc

import pydantic
from tornado import httputil, routing, web

from pydantictornado import api, handlers, openapi


class ErrorResponse(pydantic.BaseModel):
    status: int
    title: str


class BadRequestErrorResponse(ErrorResponse):
    status: int = 400
    title: str = 'Bad Request'


class InvalidItemErrorResponse(BadRequestErrorResponse):
    item_index: int = pydantic.Field(exclude=True)
    item_count: int = pydantic.Field(exclude=True)

    @pydantic.computed_field(description='Human presentable error message')
    def detail(self) -> str:
        return (
            f'Item index {self.item_index} out of range for order'
            f' with {self.item_count} items'
        )


class OrderAlreadyPaidResponse(ErrorResponse):
    status: int = 409
    title: str = 'Order Already Paid'


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


class Additions(enum.StrEnum):
    _cost: float

    def __new__(cls, description: str, cost: float) -> typing.Self:
        obj = str.__new__(cls, description)
        obj._value_ = description
        setattr(obj, '_cost', cost)  # noqa: B010
        return obj

    SHOT = 'shot', 1.00

    @property
    def cost(self) -> float:
        return self._cost


class Item(pydantic.BaseModel):
    drink: DrinkType
    size: DrinkSize
    additions: list[Additions] = pydantic.Field(default_factory=list)

    @pydantic.model_validator(mode='after')
    def validate_drink(self) -> typing.Self:
        try:
            MENU[self.drink][self.size]
        except KeyError:
            raise ValueError('Item not found in menu') from None
        return self

    @pydantic.computed_field  # type: ignore[prop-decorator]
    @property
    def price(self) -> float:
        price = MENU[self.drink][self.size]
        for addition in self.additions:
            price += addition.cost
        return price


class Order(pydantic.RootModel[list[Item]]):
    def __len__(self) -> int:
        return len(self.root)

    def __iter__(self) -> abc.Iterator[Item]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, item: int) -> Item:
        return self.root[item]


class HttpMethod(enum.StrEnum):
    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    DELETE = 'DELETE'


class OrderActionName(enum.StrEnum):
    CREATE_ORDER = 'create_order'
    PAY = 'pay'
    UPDATE_ORDER = 'update_order'


class OrderAction(pydantic.BaseModel):
    name: OrderActionName
    method: HttpMethod
    href: str = pydantic.Field(pattern='^/.*$')


class ActiveOrder(pydantic.BaseModel):
    order_id: int
    state: typing.Literal['open', 'paid', 'fulfilled'] = 'open'
    items: list[Item] = pydantic.Field(default_factory=list)
    actions: dict[str, OrderAction] = pydantic.Field(default_factory=dict)

    def add_action(
        self, name: OrderActionName, method: HttpMethod, href: str
    ) -> None:
        self.actions[name] = OrderAction.model_validate(
            {
                'name': name,
                'method': method,
                'href': href,
            }
        )

    def remove_action(self, name: OrderActionName) -> None:
        self.actions.pop(name, None)

    @pydantic.computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return sum(item.price for item in self.items)


class UpdateOrderItem(pydantic.BaseModel):
    update_type: typing.Literal['update_item']
    item_index: int
    additions: list[Additions]


class AddItemToOrder(pydantic.BaseModel):
    update_type: typing.Literal['add_item']
    item: Item


class RemoveItemFromOrder(pydantic.BaseModel):
    update_type: typing.Literal['remove_item']
    item_index: int


OrderUpdate = AddItemToOrder | RemoveItemFromOrder | UpdateOrderItem


class OrderUpdateRequest(api.Body, pydantic.RootModel[list[OrderUpdate]]):
    def __iter__(self) -> abc.Iterator[OrderUpdate]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, item: int) -> OrderUpdate:
        return self.root[item]


class Payment(api.Body, pydantic.BaseModel):
    card_number: str = pydantic.Field(alias='cardNo')
    expires: str = pydantic.Field(pattern=r'^\d{2}/\d{2}$')
    name: str
    amount: float


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


class OrderManager:
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__package__).getChild('OrderManager')
        self.orders: dict[int, ActiveOrder] = {}
        self._order_id = 1

    def create_order(self, order: Order) -> ActiveOrder:
        order_id, self._order_id = self._order_id, self._order_id + 1
        active_order = ActiveOrder(order_id=order_id, items=order)
        self.orders[order_id] = active_order
        self.logger.info(
            'created order ID %r, current cost %.2f',
            order_id,
            active_order.total,
        )
        return active_order

    def get_order(self, order_id: int) -> ActiveOrder | None:
        return self.orders.get(order_id, None)

    def pay_for_order(self, order: ActiveOrder, payment: Payment) -> Payment:
        order.state = 'paid'
        order.remove_action(OrderActionName.UPDATE_ORDER)
        order.remove_action(OrderActionName.PAY)
        return payment

    @property
    def menu(self) -> abc.Mapping[DrinkType, abc.Mapping[DrinkSize, float]]:
        return MENU


class Application(handlers.OpenAPIApplication, OrderManager, web.Application):
    def __init__(self, **kwargs: object) -> None:
        routes: list[routing.Rule] = [
            routing.URLSpec(
                '/docs',
                handlers.OpenAPIDocHandler,
                kwargs={'spec_handler_name': 'openapi_spec'},
            ),
            routing.URLSpec(
                '/orders', CreateOrderHandler, name='create_order'
            ),
            routing.URLSpec(
                '/orders/(?P<order_id>.*)', OrderHandler, name='order_handler'
            ),
            routing.URLSpec(
                '/payments/order/(?P<order_id>.*)',
                PaymentHandler,
                name='payment_handler',
            ),
            routing.URLSpec(
                '/openapi.json',
                handlers.OpenAPISpecHandler,
                name='openapi_spec',
            ),
        ]
        super().__init__(routes, **kwargs)

        order_management = self.openapi_doc.add_tag(
            'Order Management', 'Operations related to order management'
        )
        self.tag_operation('create_order', 'POST', order_management)
        self.tag_operation('order_handler', 'GET', order_management)
        self.tag_operation('order_handler', 'PUT', order_management)
        self.tag_operation('payment_handler', 'PUT', order_management)
        self.register_error_model(400, BadRequestErrorResponse)
        self.register_error_model(422, openapi.ValidationError)
        self.add_global_error(
            404, NotFoundErrorResponse, description='Order not found'
        )

    def create_order(self, order: Order) -> ActiveOrder:
        active_order = super().create_order(order)
        active_order.add_action(
            OrderActionName.UPDATE_ORDER,
            HttpMethod.PUT,
            self.reverse_url('order_handler', active_order.order_id),
        )
        active_order.add_action(
            OrderActionName.PAY,
            HttpMethod.PUT,
            self.reverse_url('payment_handler', active_order.order_id),
        )
        return active_order


class RequestHandler(handlers.PydanticErrorHandler):
    application: Application

    def __init__(
        self,
        application: web.Application,
        request: httputil.HTTPServerRequest,
        **kwargs: object,
    ) -> None:
        super().__init__(application, request, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def options(self) -> None:
        allowed: list[str] = [
            method_name
            for method_name in self.SUPPORTED_METHODS
            if getattr(self, method_name.lower()) != self._unimplemented_method
        ]
        if allowed:
            self.set_header('Allow', ', '.join(allowed))
        self.set_status(http.HTTPStatus.NO_CONTENT)


class CreateOrderHandler(RequestHandler):
    @api.expose_operation(default_status=201, summary='Create a new order')
    @api.add_response_header(
        'Location',
        description='Canonical URL of the new order',
        required=True,
        for_status=201,
    )
    @api.add_error_response(404, description='Not Found')
    @api.add_error_response(422, description='Body validation error')
    async def post(
        self,
        body: typing.Annotated[Order, api.Body(description='Order details')],
    ) -> ActiveOrder:
        self.logger.info('doin the thing with %s', body)
        order = self.application.create_order(body)
        self.set_header(
            'location', self.reverse_url('order_handler', order.order_id)
        )
        return order


class OrderHandler(RequestHandler):
    @api.expose_operation(summary='Retrieve order details')
    async def get(self, order_id: int) -> ActiveOrder:
        self.logger.info('fetching %r', order_id)
        try:
            return self.application.orders[order_id]
        except KeyError:
            raise api.StructuredError(404, NotFoundErrorResponse()) from None

    @api.expose_operation(summary='Update an order')
    @api.add_error_response(
        400, description='Order item not found', model=InvalidItemErrorResponse
    )
    @api.add_error_response(422, description='Body validation error')
    async def put(
        self, order_id: int, *, body: OrderUpdateRequest
    ) -> ActiveOrder:
        try:
            order = self.application.orders[order_id]
        except KeyError:
            raise api.StructuredError(404, NotFoundErrorResponse()) from None

        self.logger.info('update request: %s', body)
        for update in body:
            if isinstance(update, AddItemToOrder):
                order.items.append(update.item)
            else:
                try:
                    item = order.items[update.item_index]
                except IndexError:
                    raise api.StructuredError(
                        400,
                        InvalidItemErrorResponse(
                            item_index=update.item_index,
                            item_count=len(order.items),
                        ),
                    ) from None
                if isinstance(update, RemoveItemFromOrder):
                    order.items.remove(item)
                elif isinstance(update, UpdateOrderItem):
                    item.additions.extend(update.additions)
                else:
                    typing.assert_never(update)

        return order


class PaymentHandler(RequestHandler):
    @api.expose_operation(
        summary='Pay for an order', default_status=http.HTTPStatus.ACCEPTED
    )
    @api.add_error_response(
        409, model=OrderAlreadyPaidResponse, description='Order already paid'
    )
    async def put(self, order_id: int, /, body: Payment) -> ActiveOrder:
        order = self.application.get_order(order_id)
        if order is None:
            raise api.StructuredError(404, NotFoundErrorResponse())
        if order.state != 'open':
            raise api.StructuredError(409, OrderAlreadyPaidResponse())

        self.set_status(http.HTTPStatus.ACCEPTED, reason='Payment Accepted')
        self.application.pay_for_order(order, body)
        return order


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
