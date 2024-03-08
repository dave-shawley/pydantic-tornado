---
adr:
  author: Dave Shawley
  created: 03-Mar-2024
  status: accepted
---

# Rely on Python type annotations

## Context

Type annotations are becoming increasingly popular in the Python community. The [FastAPI] framework embraced using
annotations to describe how request information is deserialized. It also relied on [Pydantic] to handle the
deserialization of request data as well as the serialization of response data. I rally appreciated this after a few
years writing HTTP APIs in [tornado]. The need for serialization helpers becomes obvious pretty quickly and led me
to create the [sprockets.mixins.mediatype] library that implements proactive content negotiation. Unfortunately the
mediatype library implements both content negotiation via [accept] & [content-type] headers and the serialization of
representations. It would be nice to decouple these aspects and make "typed" request handlers a reality in the process.

## Decision

We can use type annotations to handle a number of different cross-cutting aspects.

### Use functions for request handling

Moving the request handling logic out of `tornado.web.RequestHandler.get()` and its relatives and into freestanding
functions puts you in control of the request and response data types. The framework can look at your annotations and
"do the right thing" when it comes to making sure that your code is getting what it expects.

### Inject "state" from the handler using annotated parameters

Tornado `RequestHandler`s and `Application`s have some state and functionality that request handling logic needs.
Instead of creating new interfaces for adding headers or setting specific response codes, I decided to use the interfaces
that already exist. You simply add a parameter annotated as a `tornado.web.RequestHandler` to gain access to the
methods on the request handler class. Similarly, a parameter annotated with `tornado.web.Application` will receive
the application instance.

### Hide (de-)serialization details

I'm going to take a similar approach to the one that I took with [sprockets.mixins.mediatype] when it comes to
the low-level serialization and deserialization. In fact, I might use the library for its content handlers alone.
I want this library to handle deserializing the incoming request based on the `content-type` and serialize the
response as well. The difference is that I want to take advantage of [pydantic] for values that are not "basic"
(eg, `list`, `dict`, `str`, `int`, `float`). I also want to use it for its constrained type management.

### Let pydantic handle semantic details

Relying on [pydantic] for deserializing request bodies and serializing responses places the semantic interpretation
of content outside the content-type management. [Pydantic] takes care of knowing that the `modified_at` field in
a request is an ISO-8601 formatted date-time. The _content-type management_ portion is responsible for translating
a binary `msgpack` message into a Python `dict` containing primitive types.

## Consequences

This approach places a large emphasis on type annotation processing of user-supplied functions. Interpreting the
annotations on a function is pretty messy today ... even in Python 3.12. There are several different ways to retrieve
type information from an annotated function and they differ slightly. This will make the implementation interesting
and, perhaps, even brittle. The universe of typing-related PEPs is also a moving target. I'm betting on it being more
well-behaved than the JSON schema tooling and OpenAPI specifications.

## Rejected alternatives

### Parsing docstrings

The ability to parse information from structured docstrings using a well-known format (eg, sphinx) is one way to
figure out what a request handler method expects. I didn't like this option since it either tied me to one docstring
format or left me writing different parsers and coming up with an intermediate format. The intermediate format ends
up looking a lot like type annotations or an OpenAPI specification.

### Tying an OpenAPI specification to endpoints

Another option is to describe the endpoints in an OpenAPI specification and use the embedded JSON schema descriptions
to validate requests and process responses. This approach is possible and would work relatively well if it weren't for
one glaring problem -- JSON schema descriptions do not easily map to the Python type system... _and I am aware that
one of the goals of this library is to generate OpenAPI_. This problem can be worked around by coming up with a mapping
definition from arbitrary JSON schema to Python instances in the most recent OpenAPI specification (3.1.0).

I ultimately stepped away from this approach for a few reasons that are important to understand:

1. OpenAPI is an ever-changing specification. The 3.0 version used JSON schema for _most_ things but not everything.
This was rectified in 3.1 at the expense of having to rewrite the usage of `nullable` in your specification. The next
revision looks to be a drastic simplification which will again require rewriting your specification. Maybe after the
specifications normalize this can be made to work.
2. JSON schema definitions and the Python type system don't see eye-to-eye. This is another thing that is slowly
improving through revisions of JSON schema itself. Yes, it is a moving target as well. Consider the difference between
lists and tuples. Python lists are meant to be extended with new values. Tuples, however, tend to have a fixed and
known length. Describing a tuple containing an integer and two strings in JSON schema is a little more difficult than
you might expect.
3. Engineers are not great at writing **precise** OpenAPI descriptions. It is a little easier to write precise type
annotations since they are at least still Python and good tooling exists for checking your annotations.
4. An OpenAPI specification and the Python type system are different and _they should be_. This is more of a idealogical
argument. I truly believe that writing an OpenAPI specification that _describes_ the API and its intended usage should
concentrate on making the specification useful for documentation and machine-readable validation. Letting Python type
system details or implementation details leak into the specification muddies it plain and simple.

[FastAPI]: https://fastapi.tiangolo.com/
[Pydantic]: https://docs.pydantic.dev/
[tornado]: https://www.tornadoweb.org/
[sprockets.mixins.mediatype]: https://sprocketsmixinsmedia-type.readthedocs.io/
