import asyncio
import contextlib
import logging
import typing
import uuid

import pydantic
import tornado.web

from pydantictornado import api, handlers, openapi


class Application(handlers.OpenAPIApplication, tornado.web.Application):
    def __init__(self, **settings: object) -> None:
        super().__init__(
            [
                tornado.web.url(
                    '/docs',
                    handlers.OpenAPIDocHandler,
                    {'spec_handler_name': 'openapi_spec'},
                ),
                tornado.web.url(
                    '/openapi.json',
                    handlers.OpenAPISpecHandler,
                    name='openapi_spec',
                ),
                tornado.web.url(
                    '/items', CollectionHandler, name='create_item'
                ),
                tornado.web.url(
                    '/items/(?P<item_id>.+)', ItemHandler, name='item_handler'
                ),
            ],
            **settings,
        )
        self.db: dict[uuid.UUID, Item] = {}

        self.register_error_model(404, ItemNotFoundResponse)
        self.register_error_model(
            422, openapi.ValidationError, description='Invalid body'
        )
        self.add_global_error(
            500, ErrorResponse, description='Internal Server Error'
        )

        item_management = self.openapi_doc.add_tag(
            'Item Management', 'Operations for managing items'
        )
        self.tag_operation('create_item', 'POST', item_management)
        self.tag_operation('item_handler', 'GET', item_management)
        self.tag_operation('item_handler', 'PUT', item_management)

    def get_item(self, item_id: uuid.UUID) -> 'Item':
        try:
            return self.db[item_id]
        except KeyError:
            raise api.wrap_error(404, ItemNotFoundResponse()) from None


class ErrorResponse(pydantic.BaseModel):
    code: int
    message: str


class ItemNotFoundResponse(ErrorResponse):
    code: int = 404
    message: str = 'Item Not Found'


class CreationRequest(pydantic.BaseModel):
    name: str
    description: str


class Item(CreationRequest, pydantic.BaseModel):
    id: uuid.UUID


class CollectionHandler(tornado.web.RequestHandler):
    application: Application

    @api.expose_operation(default_status=201)
    @api.add_error_response(422)
    async def post(
        self, *, body: typing.Annotated[CreationRequest, api.Body]
    ) -> Item:
        """Create a new item

        Creates a new item based on the provided data and adds it to the
        order collection with a unique ID. The result is the live order
        including the assigned identifier.
        """
        new_item = Item(id=uuid.uuid4(), **body.model_dump(mode='python'))
        self.application.db[new_item.id] = new_item
        return new_item


class ItemHandler(tornado.web.RequestHandler):
    application: Application

    @api.expose_operation(summary='Get Item by ID')
    @api.add_error_response(404, description='Item Not Found')
    async def get(self, item_id: str) -> Item:
        return self.application.get_item(uuid.UUID(item_id))

    @api.expose_operation(summary='Update Item by ID')
    @api.add_error_response(404, description='Item Not Found')
    @api.add_error_response(422)
    async def put(
        self, item_id: str, *, body: typing.Annotated[Item, api.Body]
    ) -> Item:
        old_item = self.application.get_item(uuid.UUID(item_id))
        new_item = Item(id=old_item.id, **body.model_dump(mode='python'))
        if new_item.id != old_item.id:
            del self.application.db[old_item.id]
        self.application.db[new_item.id] = new_item
        return new_item


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
