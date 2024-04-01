---
title: Injecting information
---

In vanilla Tornado, a [tornado.web.RequestHandler][] has methods to communicate
with the client. The request information is implicitly available as `self.request`.
You send response headers with `self.add_header()`, set the response code with
`self.set_status()`, etc. This functionality is *lost* when we move away from a
handler class. This library uses type annotations on parameters as tags that inject
access to the [tornado.web.Application][] and [tornado.web.RequestHandler][] instances.
See [Rely on Python type annotations](../adr/0003-type-annotations.md) for the rationale
behind the decision.

## Request information

### Path parameters

Path parameters are identified as keyword parameters by name. The values are converted
from strings to Python values based on the type annotation.

### Request body

You can access a deserialized version of the request body by adding a parameter that is
typed as a Pydantic object.

!!! warning

    **All** parameters that are typed as [pydantic.BaseModel][] subclasses are
    deserialized. If you include multiple model parameters, then the request body
    is deserialized multiple times and **must** match each model type.

You can also access the request by injecting a [tornado.httputil.HTTPServerRequest][]
parameter and using the `body` property.

### Everything else

| Annotation                           | Description                                                       |
|--------------------------------------|-------------------------------------------------------------------|
| `tornado.httputil.HTTPServerRequest` | The request being processed                                       |
| `tornado.web.Application`            | The `application` attribute from the request handler              |
| `tornado.web.RequestHandler`         | The [tornado.web.RequestHandler][] that is processing the request |

You can access most of the traditional Tornado interface by injecting a
[tornado.web.RequestHandler][] parameter.

## Supported parameter types

### Standard types

| Annotation              | Parsing details                                                                                                                            |
|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `float`                 | Floating point numbers are parsed using the [float][] constructor                                                                          |
| `int`                   | Integer numbers are parsed using the [int][] constructor as a decimal number (base 10)                                                     |
| `str`                   | String instances are passed through as-is                                                                                                  |
| `uuid.UUID`             | UUID instances are parsed using the [uuid.UUID][] constructor with the string value                                                        |
| `datetime.datetime`     | Date-times are parsed using [datetime.datetime.fromisoformat][]                                                                            |
| `datetime.date`         | Dates are parsed using [datetime.datetime.fromisoformat][] and a few hard-coded format strings to come very close to the ISO-8601 standard |
| `ipaddress.IPv4Address` | IPv4 addresses are parsed by calling the [ipaddress.IPv4Address][] constructor with the string value                                       |
| `ipaddress.IPv6Address` | IPv6 addresses are parsed by calling the [ipaddress.IPv6Address][] constructor with the string value                                       |

### Boolean values

Boolean values are problematic when it comes to internationalization. Many libraries
omit support for them in query parameters for this reason. I took a slightly different
approach. Boolean values are parsed as integers and anything non-zero is converted to
[True][]. This is probably surprising and likely doesn't work for your application.
That's fine. There are two global `set[str]` values that you can modify to set which
strings are considered *truthy* and *falsy*.

#### ::: pydantictornado.util.BOOLEAN_FALSE_STRINGS

#### ::: pydantictornado.util.BOOLEAN_TRUE_STRINGS

!!! warning

    I suspect that these will be moved into a configuration object at some point in
    the future.
