# Pydantic Tornado

> This library is pre-alpha quality at best. It will mature but I can't promise as to how
> quickly.

This library brings some of the niceties of [fastapi] to Tornado applications. Instead of writing
request handlers as stateful class instances, you write functions that use [pydantic] to define
request and response bodies. This library takes care of the serialization and deserialization
details for you. My goal is to write HTTP API servers that look a lot like [FastAPI] while reusing
the pile of Tornado-based code that I'm already entrenched in.

## Goals of this library

1. write Tornado request handlers as type annotated functions
2. hide the serialization and deserialization details
3. expose OpenAPI specification that is usable and correct

I want writing Tornado handlers to look more like free-functions that happen to have some Tornado
data intermixed with them. Something like the following would be ideal.

```python
import asyncio
import datetime
import uuid

import pydantic
from pydantictornado import routing
from tornado import web

class CreateThingyRequest(pydantic.BaseModel):
    name: str
    description: str | None

class Thingy(CreateThingyRequest):
    id: uuid.UUID
    created_at: datetime.datetime

async def create_thingy(create_request: CreateThingyRequest, *,
                        app: web.Application) -> Thingy:
    row = await app.database.execute(
        'INSERT INTO things (name, description)'
        '     VALUES (%(name)s, %(description)s)'
        '  RETURNING id, created_at',
        name=create_request.name,
        description=create_request.description,
    )
    return Thingy(id=row['id'], created_at=row['created_at'],
                  name=create_request.name,
                  description=create_request.description)

async def main() -> None:
    app = web.Application([routing.Route(r'/things', post=create_thingy)])
    app.database = ...
    app.listen(8888)
    await asyncio.Event().wait()
```

This library will inspect the annotations on the `create_thingy` function, deserialize the request
body, validate it using `CreateThingyRequest.model_validate(body)`, and call `create_thingy` with
the validated request body and a handle to the `tornado.web.Application`. The `Application` instance
is where you hide "singletons" like database connection pools. The return value is serialized to
whatever content type the client requests.

The result is much cleaner and focused application code. It's even better that most code can be tested
without using the full HTTP stack used in `tornado.testing`. Another thing to note is that the application
code doesn't interact with the `pydantictornado` library either!

At the same time, you use the `tornado.web.Application` instance for settings and things that need to
live for the entire application lifetime. You can get access to the `tornado.httputil.HTTPServerRequest`
and the active `tornado.web.RequestHandler` by adding parameters and annotating them. This is almost as
clean as FastAPI dependency injection. I explicitly chose to keep the Tornado interfaces instead of
trying to create new interfaces for things that already exist. I also want to keep everything as simple
as possible, so I am not creating the larger dependency injection framework that FastAPI uses. Don't get
me wrong, *I really like the flexibility that it creates*. I don't like the magical feeling of the caching
that `fastapi.Depends()` includes or the `dependency_overrides` dictionary.

[fastapi]: https://fastapi.tiangolo.com/
[pydantic]: https://docs.pydantic.dev/2.5/
