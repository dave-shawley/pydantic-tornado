import datetime
import ipaddress
import typing
import unittest
import uuid

import pydantic
import yarl
from pydantictornado import openapi


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
