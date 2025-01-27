import enum
import typing
from collections import abc

import pydantic

from pydantictornado import api


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
        from examples.coffee import orders

        try:
            orders.MENU[self.drink][self.size]
        except KeyError:
            raise ValueError('Item not found in menu') from None
        return self

    @pydantic.computed_field  # type: ignore[prop-decorator]
    @property
    def price(self) -> float:
        from examples.coffee import orders

        price = orders.MENU[self.drink][self.size]
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


class Payment(api.Body, pydantic.BaseModel):
    card_number: str = pydantic.Field(alias='cardNo')
    expires: str = pydantic.Field(pattern=r'^\d{2}/\d{2}$')
    name: str
    amount: float
