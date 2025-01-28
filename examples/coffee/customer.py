import http
import typing
from collections import abc

import pydantic

from examples.coffee import models, shared
from pydantictornado import api


class UpdateOrderItem(pydantic.BaseModel):
    update_type: typing.Literal['update_item']
    item_index: int
    additions: list[models.Additions]


class AddItemToOrder(pydantic.BaseModel):
    update_type: typing.Literal['add_item']
    item: models.Item


class RemoveItemFromOrder(pydantic.BaseModel):
    update_type: typing.Literal['remove_item']
    item_index: int


OrderUpdate = AddItemToOrder | RemoveItemFromOrder | UpdateOrderItem


class OrderUpdateRequest(api.Body, pydantic.RootModel[list[OrderUpdate]]):
    def __iter__(self) -> abc.Iterator[OrderUpdate]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, item: int) -> OrderUpdate:
        return self.root[item]


class CreateOrderHandler(shared.RequestHandler):
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
        body: typing.Annotated[
            models.Order, api.Body(description='Order details')
        ],
    ) -> models.ActiveOrder:
        self.logger.info('doin the thing with %s', body)
        order = self.application.create_order(body)
        self.set_header(
            'location', self.reverse_url('order_handler', order.order_id)
        )
        return order


class OrderHandler(shared.RequestHandler):
    @api.expose_operation(summary='Retrieve order details')
    async def get(self, order_id: int) -> models.ActiveOrder:
        self.logger.info('fetching %r', order_id)
        try:
            return self.application.orders[order_id]
        except KeyError:
            raise api.wrap_error(404, shared.NotFoundErrorResponse()) from None

    @api.expose_operation(summary='Update an order')
    @api.add_error_response(
        400,
        description='Order item not found',
        model=shared.InvalidItemErrorResponse,
    )
    @api.add_error_response(422, description='Body validation error')
    async def put(
        self, order_id: int, *, body: OrderUpdateRequest
    ) -> models.ActiveOrder:
        try:
            order = self.application.orders[order_id]
        except KeyError:
            raise api.wrap_error(404, shared.NotFoundErrorResponse()) from None

        self.logger.info('update request: %s', body)
        for update in body:
            if isinstance(update, AddItemToOrder):
                order.items.append(update.item)
            else:
                try:
                    item = order.items[update.item_index]
                except IndexError:
                    raise api.wrap_error(
                        400,
                        shared.InvalidItemErrorResponse(
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


class PaymentHandler(shared.RequestHandler):
    @api.expose_operation(
        summary='Pay for an order', default_status=http.HTTPStatus.ACCEPTED
    )
    @api.add_error_response(
        409,
        model=shared.OrderAlreadyPaidResponse,
        description='Order already paid',
    )
    async def put(
        self, order_id: int, /, body: models.Payment
    ) -> models.ActiveOrder:
        order = self.application.get_order(order_id)
        if order is None:
            raise api.wrap_error(404, shared.NotFoundErrorResponse())
        if order.state != 'open':
            raise api.wrap_error(409, shared.OrderAlreadyPaidResponse())

        self.set_status(http.HTTPStatus.ACCEPTED, reason='Payment Accepted')
        self.application.pay_for_order(order, body)
        return order
