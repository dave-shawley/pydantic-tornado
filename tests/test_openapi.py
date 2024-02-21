import typing
import unittest

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
