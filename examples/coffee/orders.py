import logging
from collections import abc

from examples.coffee import models

MENU: dict[models.DrinkType, dict[models.DrinkSize, float]] = {
    models.DrinkType.AMERICANO: {
        models.DrinkSize.SHORT: 3.65,
        models.DrinkSize.TALL: 3.75,
        models.DrinkSize.GRANDE: 3.95,
        models.DrinkSize.VENTI: 4.25,
    },
    models.DrinkType.CAPPUCCINO: {
        models.DrinkSize.SHORT: 4.55,
        models.DrinkSize.TALL: 4.65,
        models.DrinkSize.GRANDE: 5.25,
        models.DrinkSize.VENTI: 5.65,
    },
    models.DrinkType.ESPRESSO: {
        models.DrinkSize.SOLO: 2.75,
        models.DrinkSize.DOPPIO: 2.95,
        models.DrinkSize.TRIPLE: 3.45,
        models.DrinkSize.QUAD: 3.85,
    },
    models.DrinkType.LATTE: {
        models.DrinkSize.SHORT: 4.55,
        models.DrinkSize.TALL: 4.65,
        models.DrinkSize.GRANDE: 5.25,
        models.DrinkSize.VENTI: 5.65,
    },
}


class OrderManager:
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__package__).getChild('OrderManager')
        self.orders: dict[int, models.ActiveOrder] = {}
        self._order_id = 1

    def create_order(self, order: models.Order) -> models.ActiveOrder:
        order_id, self._order_id = self._order_id, self._order_id + 1
        active_order = models.ActiveOrder(order_id=order_id, items=order)
        self.orders[order_id] = active_order
        self.logger.info(
            'created order ID %r, current cost %.2f',
            order_id,
            active_order.total,
        )
        return active_order

    def get_order(self, order_id: int) -> models.ActiveOrder | None:
        return self.orders.get(order_id, None)

    def pay_for_order(
        self, order: models.ActiveOrder, payment: models.Payment
    ) -> models.Payment:
        order.state = 'paid'
        order.remove_action(models.OrderActionName.UPDATE_ORDER)
        order.remove_action(models.OrderActionName.PAY)
        return payment

    @property
    def menu(
        self,
    ) -> abc.Mapping[models.DrinkType, abc.Mapping[models.DrinkSize, float]]:
        return MENU
