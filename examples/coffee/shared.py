import http
import logging
import typing

import pydantic
from tornado import httputil, web

from pydantictornado import handlers

if typing.TYPE_CHECKING:
    import examples.coffee.application


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


class RequestHandler(handlers.PydanticErrorHandler):
    application: 'examples.coffee.application.Application'

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
