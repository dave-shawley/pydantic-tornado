from tornado import routing, web

from examples.coffee import customer, orders, shared
from pydantictornado import handlers, openapi


class Application(
    handlers.OpenAPIApplication, orders.OrderManager, web.Application
):
    def __init__(self, **kwargs: object) -> None:
        routes: list[routing.Rule] = [
            routing.URLSpec(
                '/docs',
                handlers.OpenAPIDocHandler,
                kwargs={'spec_handler_name': 'openapi_spec'},
            ),
            routing.URLSpec(
                '/orders', customer.CreateOrderHandler, name='create_order'
            ),
            routing.URLSpec(
                '/orders/(?P<order_id>.*)',
                customer.OrderHandler,
                name='order_handler',
            ),
            routing.URLSpec(
                '/payments/order/(?P<order_id>.*)',
                customer.PaymentHandler,
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
        self.register_error_model(400, shared.BadRequestErrorResponse)
        self.register_error_model(422, openapi.ValidationError)
        self.add_global_error(
            404, shared.NotFoundErrorResponse, description='Order not found'
        )
