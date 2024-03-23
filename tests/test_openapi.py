import datetime
import ipaddress
import re
import typing
import unittest
import uuid

import pydantic
import tornado.routing
import yarl
from pydantictornado import openapi, routing
from tornado import httputil, web


class DescribeTypeTests(unittest.TestCase):
    def test_describing_primitive_types(self) -> None:
        self.assertEqual({'type': 'boolean'}, openapi.describe_type(bool))
        self.assertEqual({'type': 'integer'}, openapi.describe_type(int))
        self.assertEqual({'type': 'number'}, openapi.describe_type(float))
        self.assertEqual({'type': 'string'}, openapi.describe_type(str))
        self.assertEqual({'type': 'array'}, openapi.describe_type(list))
        self.assertEqual({'type': 'array'}, openapi.describe_type(set))
        self.assertEqual({'type': 'array'}, openapi.describe_type(tuple))
        self.assertEqual({'type': 'null'}, openapi.describe_type(None))
        self.assertEqual({'type': 'null'}, openapi.describe_type(type(None)))

        with self.assertRaises(ValueError):
            openapi.describe_type(object)
        with self.assertRaises(NotImplementedError):
            openapi.describe_type(dict)

    def test_describining_library_types(self) -> None:
        self.assertEqual(
            {'type': 'string', 'format': 'date'},
            openapi.describe_type(datetime.date),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'date-time'},
            openapi.describe_type(datetime.datetime),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'time'},
            openapi.describe_type(datetime.time),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'duration'},
            openapi.describe_type(datetime.timedelta),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'ipv4'},
            openapi.describe_type(ipaddress.IPv4Address),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'ipv6'},
            openapi.describe_type(ipaddress.IPv6Address),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'uuid'},
            openapi.describe_type(uuid.UUID),
        )
        self.assertEqual(
            {'type': 'string', 'format': 'uri'},
            openapi.describe_type(yarl.URL),
        )

    def test_describing_typed_collections(self) -> None:
        self.assertEqual(
            {'type': 'array', 'items': {'type': 'string'}},
            openapi.describe_type(list[str]),
        )
        self.assertEqual(
            {
                'type': 'array',
                'items': {'anyOf': [{'type': 'integer'}, {'type': 'string'}]},
            },
            openapi.describe_type(list[int | str]),
        )
        result = openapi.describe_type(list[int | str | tuple[str]])
        self.assertEqual(
            {
                'type': 'array',
                'items': {
                    'anyOf': [
                        {'type': 'integer'},
                        {'type': 'string'},
                        {
                            'type': 'array',
                            'prefixItems': [{'type': 'string'}],
                            'items': False,
                            'minItems': 1,
                        },
                    ]
                },
            },
            result,
        )
        result = openapi.describe_type(tuple[str, int, float])
        self.assertEqual(
            {
                'type': 'array',
                'prefixItems': [
                    {'type': 'string'},
                    {'type': 'integer'},
                    {'type': 'number'},
                ],
                'items': False,
                'minItems': 3,
            },
            result,
        )

    def test_complex_cases(self) -> None:
        result = openapi.describe_type(list[tuple[str, int] | list[str]])
        self.assertEqual(
            {
                'type': 'array',
                'items': {
                    'anyOf': [
                        {
                            'type': 'array',
                            'prefixItems': [
                                {'type': 'string'},
                                {'type': 'integer'},
                            ],
                            'items': False,
                            'minItems': 2,
                        },
                        {
                            'type': 'array',
                            'items': {'type': 'string'},
                        },
                    ]
                },
            },
            result,
        )

    def test_special_forms(self) -> None:
        self.assertEqual(
            {'type': 'string'}, openapi.describe_type(typing.LiteralString)
        )
        self.assertEqual(
            {'type': 'integer', 'const': 1},
            openapi.describe_type(typing.Literal[1]),
        )
        self.assertEqual(
            {
                'anyOf': [
                    {'type': 'integer', 'const': 1},
                    {'type': 'boolean', 'const': True},
                    {'type': 'string', 'const': 'yes'},
                ]
            },
            openapi.describe_type(typing.Literal[1, True, 'yes']),
        )

    def test_illegal_types(self) -> None:
        with self.assertWarnsRegex(RuntimeWarning, r'expected exactly one'):
            self.assertEqual(
                {'type': 'array', 'items': {'type': 'integer'}},
                openapi.describe_type(list[int, int]),  # type: ignore[misc]
            )

        with self.assertRaises(TypeError):
            openapi.describe_type(12)

        class SomeProtocol(typing.Protocol):
            def some_method(self) -> None:
                ...

        with self.assertRaises(ValueError):
            openapi.describe_type(SomeProtocol)

    def test_pydantic_support(self) -> None:
        class Widget(pydantic.BaseModel):
            id: uuid.UUID  # noqa: A003 - shadows builtin
            name: str
            created_at: datetime.datetime

        self.assertEqual(
            Widget.model_json_schema(), openapi.describe_type(Widget)
        )
        self.assertEqual(
            {'type': 'array', 'items': Widget.model_json_schema()},
            openapi.describe_type(list[Widget]),
        )
        self.assertEqual(
            {
                'anyOf': [
                    {'type': 'string', 'format': 'uuid'},
                    Widget.model_json_schema(),
                ]
            },
            openapi.describe_type(uuid.UUID | Widget),
        )


class OpenAPIAnnotationTests(unittest.TestCase):
    def test_extra(self) -> None:
        self.assertEqual(
            {'type': 'string', 'format': 'uuid', 'title': 'Unique identifier'},
            openapi.describe_type(
                typing.Annotated[
                    uuid.UUID,
                    openapi.SchemaExtra(title='Unique identifier'),
                ]
            ),
        )

    def test_multiple_extra(self) -> None:
        self.assertEqual(
            {
                'title': 'Unique identifier',
                'description': 'Uniquely identifies the thing',
                'type': 'string',
                'format': 'uuid',
            },
            openapi.describe_type(
                typing.Annotated[
                    uuid.UUID,
                    openapi.SchemaExtra(title='Unique identifier'),
                    openapi.SchemaExtra(
                        description='Uniquely identifies the thing'
                    ),
                ]
            ),
        )

    def test_other_annotations_ignored(self) -> None:
        class OtherAnnotation:
            pass

        self.assertEqual(
            {'type': 'string', 'format': 'uuid', 'title': 'Unique identifier'},
            openapi.describe_type(
                typing.Annotated[
                    uuid.UUID,
                    openapi.SchemaExtra(title='Unique identifier'),
                    OtherAnnotation(),
                ]
            ),
        )

    def test_extras_at_multiple_levels(self) -> None:
        price = typing.Annotated[float, openapi.SchemaExtra(title='Item cost')]
        default_price = typing.Annotated[
            price,
            openapi.SchemaExtra(default=0.99),
            openapi.SchemaExtra(title='Default item cost'),
        ]
        self.assertEqual(
            {
                'type': 'array',
                'prefixItems': [
                    {
                        'title': 'Default item cost',
                        'type': 'number',
                        'default': 0.99,
                    },
                    {
                        'type': 'array',
                        'items': {'type': 'number', 'title': 'Item cost'},
                    },
                ],
                'minItems': 2,
                'items': False,
            },
            openapi.describe_type(tuple[default_price, list[price]]),
        )


class OpenAPIRegexTests(unittest.TestCase):
    @staticmethod
    def translate_path_pattern(pattern: str) -> openapi.OpenAPIPath:
        return openapi._translate_path_pattern(re.compile(pattern))

    def test_simple_paths(self) -> None:
        result = self.translate_path_pattern(r'/items/(?P<item_id>.*)')
        self.assertEqual('/items/{item_id}', result.path)
        self.assertEqual('.*', result.patterns['item_id'])

        result = self.translate_path_pattern(
            r'/projects/(?P<id>[1-9]\d*)/facts/(?P<fact_id>\d+)'
        )
        self.assertEqual('/projects/{id}/facts/{fact_id}', result.path)
        self.assertEqual(r'[1-9]\d*', result.patterns['id'])
        self.assertEqual(r'\d+', result.patterns['fact_id'])

        result = self.translate_path_pattern('/status/')
        self.assertEqual('/status/', result.path)
        self.assertDictEqual({}, result.patterns)

    def test_path_cleanup(self) -> None:
        result = self.translate_path_pattern('/status/?')
        self.assertEqual('/status', result.path)
        self.assertDictEqual({}, result.patterns)

        result = self.translate_path_pattern('/status$')
        self.assertEqual('/status', result.path)
        self.assertDictEqual({}, result.patterns)

        result = self.translate_path_pattern('/status/?$')
        self.assertEqual('/status', result.path)
        self.assertDictEqual({}, result.patterns)

        result = self.translate_path_pattern('/status?$')
        self.assertEqual('/status?', result.path)
        self.assertDictEqual({}, result.patterns)

        result = self.translate_path_pattern('/?$')
        self.assertEqual('/', result.path)
        self.assertDictEqual({}, result.patterns)

    def test_noncapture_removals(self) -> None:
        result = self.translate_path_pattern(
            r'/(?P<ipv4>[1-9][0-9]{0,2}(?:\.[1-9][0-9]+){3})'
        )
        self.assertEqual('/{ipv4}', result.path)
        self.assertEqual(
            r'[1-9][0-9]{0,2}(\.[1-9][0-9]+){3}', result.patterns['ipv4']
        )

        result = self.translate_path_pattern(r'/(?P<val>(?:outer(?:inner)))')
        self.assertEqual('/{val}', result.path)
        self.assertEqual('(outer(inner))', result.patterns['val'])

    def test_comment_removal(self) -> None:
        result = self.translate_path_pattern(
            r'/prefix(?#are these used?)/suffix'
        )
        self.assertEqual('/prefix/suffix', result.path)
        self.assertDictEqual({}, result.patterns)

        result = self.translate_path_pattern(
            r'/sleep/(?P<delay>(?#in millis)\d+)'
        )
        self.assertEqual('/sleep/{delay}', result.path)
        self.assertEqual(r'\d+', result.patterns['delay'])

    def test_back_references(self) -> None:
        result = self.translate_path_pattern(r'/(?P<start>one)/two/(?P=start)')
        self.assertEqual('/{start}/two/{start}', result.path)
        self.assertEqual('one', result.patterns['start'])

    def test_unhandled_cases_emit_warnings(self) -> None:
        unhandled = [
            r'/Isaac(?=Asimov)',
            r'/Isaac(?!Asimov)',
            r'/(?<=-)\w+',
            r'/(?<!abc)def',
        ]
        for pattern in unhandled:
            with self.assertWarns(UserWarning):
                self.translate_path_pattern(pattern)


class DescribeApiTests(unittest.TestCase):
    def test_application_without_routes(self) -> None:
        description = openapi.describe_api(web.Application())
        self.assertEqual('3.1.0', description.openapi)
        self.assertEqual({}, description.info)
        self.assertEqual(
            'https://spec.openapis.org/oas/3.1/dialect/base',
            description.jsonSchemaDialect,
        )
        self.assertEqual([], description.servers)
        self.assertEqual({}, description.paths)
        self.assertEqual({}, description.components)
        self.assertEqual([], description.tags)

    def test_application_with_our_routes(self) -> None:
        # fmt: off
        async def delay(_v: float) -> None: pass
        async def find_item(_id: uuid.UUID) -> None: pass
        async def status() -> dict[str, str]: return {}
        # fmt: on

        app = web.Application(
            [
                routing.Route(r'/delay/(?P<_v>\d(?:\.\d+))', get=delay),
                routing.Route(r'/items/(?P<_id>.*)', get=find_item),
                routing.Route(r'/status', get=status),
            ]
        )
        description = openapi.describe_api(app).model_dump(by_alias=True)
        self.assertSetEqual(
            {'/delay/{_v}', '/status', '/items/{_id}'},
            set(description['paths'].keys()),
        )

        self.assertNotIn('parameters', description['paths']['/status'])
        self.assertEqual(
            1, len(description['paths']['/delay/{_v}']['parameters'])
        )
        self.assertDictEqual(
            {
                'name': '_v',
                'in': 'path',
                'required': True,
                'deprecated': False,
                'schema': {'type': 'float'},
            },
            description['paths']['/delay/{_v}']['parameters'][0],
        )

        self.assertEqual(
            1, len(description['paths']['/items/{_id}']['parameters'])
        )
        self.assertDictEqual(
            {
                'name': '_id',
                'in': 'path',
                'required': True,
                'deprecated': False,
                'schema': {
                    'type': 'string',
                    'pattern': '.*',
                    'format': 'uuid',
                },
            },
            description['paths']['/items/{_id}']['parameters'][0],
        )

    def test_application_with_urlspec_routes(self) -> None:
        app = web.Application(
            [web.url(r'/items/(?P<id>.*)', web.RequestHandler)]
        )
        description = openapi.describe_api(app).model_dump(by_alias=True)
        self.assertSetEqual(
            {'/items/{id}'},
            set(description['paths'].keys()),
        )
        self.assertEqual(
            1, len(description['paths']['/items/{id}']['parameters'])
        )
        self.assertDictEqual(
            {
                'name': 'id',
                'in': 'path',
                'required': True,
                'deprecated': False,
                'schema': {'type': 'string', 'pattern': '.*'},
            },
            description['paths']['/items/{id}']['parameters'][0],
        )

    def test_other_route_types(self) -> None:
        class MethodMatcher(tornado.routing.Matcher):
            def __init__(self, http_method: str) -> None:
                self.method = http_method

            def match(
                self, request: httputil.HTTPServerRequest
            ) -> None | dict[str, object]:
                if request.method == self.method:
                    return {}
                return None

        class MethodMatchingRoute(tornado.routing.Rule):
            def __init__(self) -> None:
                super().__init__(MethodMatcher('GET'), object())

        app = web.Application([MethodMatchingRoute()])
        with self.assertWarns(UserWarning):
            description = openapi.describe_api(app)
        self.assertEqual(0, len(description.paths))


class DescribePathTests(unittest.TestCase):
    def test_mixed_annotations(self) -> None:
        IdType = typing.Annotated[  # noqa: N806 -- variable named as class
            int,
            openapi.SchemaExtra(summary='Unique item identifer'),
            routing.ParameterAnnotation(description='More information here'),
            'another annotation that we ignore',
        ]

        async def op(item_id: IdType) -> IdType:
            return item_id

        description = openapi._describe_path(
            routing.Route(r'/items/(?P<item_id>\d+)', get=op),
            openapi.OpenAPIPath(
                path='/items/{item_id}', patterns={'item_id': r'\d+'}
            ),
        )
        self.assertEqual(
            'More information here', description.parameters[0].description
        )

    def test_union_parameters(self) -> None:
        IdType: typing.TypeAlias = int | uuid.UUID

        async def op(item_id: IdType) -> IdType:
            return item_id

        description = openapi._describe_path(  # private use ok
            routing.Route(r'/items/(?P<item_id>\d+)', get=op),
            openapi.OpenAPIPath(
                path='/items/{item_id}', patterns={'item_id': r'\d+'}
            ),
        )
        self.assertEqual(
            {'oneOf': [{'type': 'int'}, {'type': 'string', 'format': 'uuid'}]},
            description.parameters[0].schema_,
        )

        async def another_op(item_id: IdType | None) -> IdType | None:
            return item_id

        description = openapi._describe_path(  # private use ok
            routing.Route(r'/items/(?P<item_id>\d+)', get=another_op),
            openapi.OpenAPIPath(
                path='/items/{item_id}', patterns={'item_id': r'\d+'}
            ),
        )
        self.assertEqual(
            {
                'oneOf': [
                    {'type': 'int'},
                    {'type': 'string', 'format': 'uuid'},
                    {'type': 'null'},
                ]
            },
            description.parameters[0].schema_,
        )
