---
title: Examples
---

Sometimes it is easiest to see how something is done rather than being told how to do it.
We're going to start with that approach... the following sections show how to do various
things in separate request handling functions. Here is the application file that each
request handler is added to.

```python
import signal
import asyncio
import contextlib
import logging

from pydantictornado import routing
from tornado import web

async def handler():
    ...

async def main() -> None:
    running = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGINT, running.set)

    app = web.Application([
        routing.Route('/', get=handler),
    ])
    app.listen(8888)
    with contextlib.suppress(KeyboardInterrupt):
        await running.wait()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)1.1s %(name)s: %(message)s')
```

## Simple route returning a JSON object

Whatever a request handler returns is passed to [tornado.web.RequestHandler.write][]
after serializing it with a slightly extended [json.dumps][].

```python
import datetime

async def handler() -> dict[str, datetime.datetime]:
    return {'now': datetime.datetime.now(datetime.UTC)}
```

!!! note

    The return type annotation is not currently used but it will be in the future!

```http
GET / HTTP/1.1
Host: 127.0.0.1:8888
Accept: */*
```
```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=UTF-8
Content-Length: 43

{"now": "2024-03-03T20:25:50.286858+00:00"}
```

## Setting a status code

Tornado uses a method on the `RequestHandler` to set the response status code, so you need
to request access to it. Simply include any parameter with a type annotation of
`tornado.web.RequestHandler`. When your function is called, it will be passed an instance
to the request handler that received the request.

```python
from tornado import web

async def handler(h: web.RequestHandler) -> None:
    h.set_status(204)
```

```http
GET / HTTP/1.1
Host: 127.0.0.1:8888
Accept: */*
```

```http
HTTP/1.1 204 No Content

```

The parameter can be named whatever you want and can appear *anywhere* in the parameter
list. In fact, *any* parameter annotated with `RequestHandler` will receive the same value.
You can even include a default value if you want -- this can be useful if you want to use
the same function for more than one HTTP resource.

!!! note

    This usage of type annotations is used to inject access to various framework objects
    into your request handlers. Many thanks to [FastAPI] for introducing me to this
    technique.

[FastAPI]: https://fastapi.tiangolo.com/

## Processing path parameters

This is almost identical to how path parameters work in vanilla Tornado. The difference is
that parameters are *converted to Python objects* instead of passing strings.

```python
import uuid

async def handler(item_id: uuid.UUID) -> dict[str, object]:
    return {'value': item_id}
```

```http
GET /00000000-0000-0000-0000-000000000000 HTTP/1.1
Host: 127.0.0.1:8888
Accept: */*
```

```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=UTF-8
Content-Length: 46

{"id": "00000000-0000-0000-0000-000000000000"}
```

Of course, you will have to add the variable to the path expression in the `route.Route` line.

```diff
     app = web.Application([
-       routing.Route('/', get=handler),
+       routing.Route('/(?P<item_id>.*)', get=handler),
     ])
```

That looks almost identical to the Tornado handler other than the type exception. Let's see
how it differs by requesting `/not-a-uuid`:

```http
HTTP/1.1 400 Bad Request
Content-Type: text/html; charset=UTF-8
Content-Length: 73

<html><title>400: Bad Request</title><body>400: Bad Request</body></html>
```

Your request handler is never invoked in this case since the type conversion fails. The error
reporting defaults to Tornado's standard behaviour of returning an HTML document. We will come
back to this later.
