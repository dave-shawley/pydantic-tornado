---
title: Pydantic-tornado
hide:
  - navigation
---

!!! warning

    **DO NOT USE THIS LIBRARY ANYWHERE THAT WILL AFFECT CUSTOMERS OR BUSINESSES**

    I reserve the right to stop working on this, change the name, completely reorganize the library, and
    pretty much anything else at any time.

This library brings some of the niceties of [fastapi] to Tornado applications. Instead of writing
request handlers as stateful class instances, you write functions that use [pydantic] to define
request and response bodies. This library takes care of the serialization and deserialization
details for you. My goal is to write HTTP API servers that look a lot like [FastAPI] while reusing
the pile of Tornado-based code that I'm already entrenched in.

## Goals of this library

1. write Tornado request handlers as type annotated functions
2. hide the serialization and deserialization details
3. expose OpenAPI specification that is usable and correct

## Current status

*Extremely immature* is a pretty good description. **Incomplete** and **untested** is another way to say it.
I really want this to go past the vapourware stage, but it is little more than a proof-of-concept currently.

=== "Routing"

    - [X] Named path parameter handling
    - [ ] Positional path parameter handling
    - [ ] Pydantic parameter serialization

=== "Request processing"

    - [X] Injection of Tornado handling context
    - [ ] Pydantic request body deserialization

=== "Response processing"

    - [X] JSON responses from simple types
    - [ ] [Proactive content negotiation](https://www.rfc-editor.org/rfc/rfc7231#section-3.4.1)
    - [ ] Pydantic response serialization

=== "OpenAPI"

    - [X] OpenAPI schema from basic types
    - [ ] OpenAPI schema from Pydantic
    - [ ] Handler for generated OpenAPI JSON

## Sales pitch

Consider the following naive implementation of a simple CRUD handler.

```python
import json
import tornado.web

class WidgetAPI(tornado.web.RequestHandler):
    async def get(self, widget_id: str) -> None:
        row = await some_database.execute(
            'SELECT id, name, description, created_at'
            '  FROM public.widgets'
            ' WHERE id = %(widget_id)s',
            {'widget_id': widget_id}
        )
        if not row:
            raise tornado.web.HTTPError(404)
        self.write(json.dumps(row))
    async def post(self) -> None:
        details = json.loads(self.request.body.decode('utf-8'))
        row = await some_database.execute(
            'INSERT INTO public.widgets (name, description,'
            '                            created_at)'
            '     VALUES (%(name)s, %(description)s,'
            '             CURRENT_TIMESTAMP)'
            '  RETURNING id, name, description',
            {'name': details['name'],
             'description': details['description']}
        )
        self.write(json.dumps(row))
```

Now, quickly answer a few questions:

1. is the response body consistent between methods?
2. what is the type of `id`?

We can answer the first of the questions but not the second. What if we could rewrite this example as:

```python
import datetime
import uuid

import pydantic


class CreateWidgetRequest(pydantic.BaseModel):
    name: str
    description: str


class Widget(CreateWidgetRequest):
    id: pydantic.UUID4
    name: str
    description: str
    created_at: datetime.datetime


async def get_widget(widget_id: uuid.UUID) -> Widget | None:
    row = await some_database.execute(
        'SELECT id, name, description, created_at'
        '  FROM public.widgets'
        ' WHERE id = %(widget_id)s',
        {'widget_id': widget_id}
    )
    if row:
        return Widget.model_validate(row)


async def create_widget(widget_details: CreateWidgetRequest) -> Widget:
    row = await some_database.execute(
        'INSERT INTO public.widgets (name, description,'
        '                            created_at)'
        '     VALUES (%(name)s, %(description)s,'
        '             CURRENT_TIMESTAMP)'
        '  RETURNING id, name, description, created_at',
        widget_details.dict()
    )
    return Widget.model_validate(row)
```

Yes ... there is a bit more code there.  Sorry / not sorry. Oh, there is a missing chunk in both
snippets -- _adding the routes to the application_. I'll skip the traditional example. Here is what I am
envisioning for the new code.

```python
from tornado import web
from pydantictornado import routing

class Application(web.Application):
    def __init__(self, **settings):
        super().__init__([
            routing.Route('/widgets', post=create_widget),
            routing.Route('/widgets/(?P<widget_id>.*)', get=get_widget),
        ], **settings)
```

Instead of having separate [tornado.web.RequestHandler][] classes for each HTTP route, this library exposes
a function that returns a route that will send `GET`, `POST`, et al. requests to a resource to our functions.
The machinery inspects the type annotations on the callables and uses them to deserialize request bodies before
calling the function. Similarly, return annotations dictate the response types which are serialized using the
standard pydantic methods.

## More than syntax sweetening?

I agree that it is easy to think of this as simple syntactical sugar but there is more to it than that. It is
moving the request and response structures into well-defined classes. This also makes it possible to generate
OpenAPI specifications _directly from the annotated functions._ No need to embed OpenAPI specs in docstrings or
write cumbersome YAML files.

And ... **yes** ... I am stealing the concepts from FastAPI. After working with FastAPI for a little while, I
decided that the structure is really pleasant to work with. Not having the deal with serialization or keeping API
documentation in sync is a great boon to developer productivity. Injecting parameters based on type annotations
is a little magical at first -- think of passing light through a prism as opposed to a natural double rainbow.
It quickly becomes second nature and does the right thing. It can be simpler than what FastAPI uses by constraining
the functionality slightly. You'll see what I'm talking about.


[fastapi]: https://fastapi.tiangolo.com/
[pydantic]: https://docs.pydantic.dev/2.5/
