---
title: Writing handlers
---

Request handlers are simply `async` functions decorated with [type annotations]. The annotations control how path
parameters are converted from strings. Annotations are also used to inject the raw request object, the
[tornado.web.RequestHandler][] that is processing the request, and other supporting objects. See
[Injecting Information](injections.md) for the full list of supported *injections*. A lot of your handlers will
look much like they do today except for the parts that interact with the request and response. Let's say you have
a request handler that processes a `POST` request by inserting some data into a database and returning the newly
created record. Here's an example without any of the pesky database stuff.

```python
import uuid
import json
from tornado import web

class MyHandler(web.RequestHandler):
    async def post(self) -> None:
        body = json.loads(self.request.body.decode('utf-8'))
        created_id = await self._insert_new_thing(self.application.db, body)
        body['id'] = str(created_id)
        self.write(body)

    async def _insert_new_thing(self, db, body) -> uuid.UUID:
        row = await db.execute(
            'INSERT INTO things(name) VALUES (:name) RETURNING id',
            name=body['name'])
        return row['id']

class Application(web.Application):
    def __init__(self, **settings) -> None:
        super().__init__(
            [web.url(r'/things', MyHandler)],
            **settings)
```

The reimplementation of the handler using pydantic for the request and response would look like the following:

```python
import uuid

import pydantic
from pydantictornado import routing
from tornado import web

class CreateRequest(pydantic.BaseModel):
    name: str

class Thingy(pydantic.BaseModel):
    id: uuid.UUID
    name: str

async def create_thingy(details: CreateRequest,
                        app: web.Application) -> Thingy:
    return await insert_new_thing(app.db, details)

async def insert_new_thing(db, details: CreateRequest) -> Thingy:
    row = await db.execute(
        'INSERT INTO things(name) VALUES (:name) RETURNING id',
        name=details.name)
    return Thingy(id=row['id'], name=details.name)

class Application(web.Application):
    def __init__(self, **settings) -> None:
        super().__init__(
            [routing.Route(r'/things', post=create_thingy)],
            **settings)
```

Yeah... once again... there is more code there. Here's what you've gained:

1. nothing before the `Application` definition uses `tornado` directly
2. the only thing that is a raw (untyped) dictionary is the row returned from the database layer
3. testing your application code doesn't require a web stack ... your implementation consists of free functions

The testing point is a little bit of a white lie. You do need to create application and request objects if your
application code needs them. If you are only accessing properties, like the `db` property, then you can test with
mocks that have the properties that you need only. After all, that is the most reliable way to ensure that your
code *does not rely on hidden functionality*.

## Okay... what aren't you telling me?

There are a number of things that I haven't worked out yet. One of the conventions in Tornado applications is to
do useful things in your `on_finish()` method like update performance counters. We also insert error handling
wrappers to convert uncaught exceptions to well-formed responses and the like. I still have to work through the
details on this stuff... *but this is pre-alpha software at this point!*.

[type annotations]: https://typing.readthedocs.io/en/latest/spec/index.html
