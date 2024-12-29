import unittest

import pydantic

from pydantictornado import api, models


class C:
    pass


class MarkerTests(unittest.TestCase):
    def test_extract_with_non_marker(self) -> None:
        obj = C()
        setattr(obj, '__pydantic_tornado_method__', 42)  # noqa: B010
        with self.assertRaises(TypeError):
            api.OpenAPIMethodInfo.extract(obj)

    def test_that_frozen_marker_is_immutable(self) -> None:
        marker = api._FrozenMethodInfo()
        with self.assertRaises(TypeError):
            marker.response_type = None
        with self.assertRaises(AttributeError):
            marker.extra.clear()


class OpenAPILogicTests(unittest.TestCase):
    def test_parameter_path_property_initialization(self) -> None:
        param = models.Parameter.model_validate(
            {'name': 'page_size', 'in': 'query', 'schema': {'type': 'number'}}
        )
        self.assertIs(param.required, False)  # noqa: FBT003 -- positional bool param ok here

        param = models.Parameter.model_validate(
            {'name': 'order_id', 'in': 'path', 'schema': {'type': 'string'}},
        )
        self.assertIs(param.required, True)  # noqa: FBT003 -- positional bool param ok here

        param = models.Parameter.model_validate(
            {
                'name': 'page_size',
                'in': 'query',
                'required': True,
                'schema': {'type': 'number'},
            },
        )
        self.assertIs(param.required, True)  # noqa: FBT003 -- positional bool param ok here

        with self.assertRaises(pydantic.ValidationError):
            models.Parameter.model_validate(
                {
                    'name': 'order_id',
                    'in': 'path',
                    'required': False,
                    'schema': {'type': 'string'},
                },
            )

    def test_parameter_style_property_initialization(self) -> None:
        expected = {
            'cookie': 'form',
            'header': 'simple',
            'path': 'simple',
            'query': 'form',
        }
        for location, style in expected.items():
            param = models.Parameter.model_validate(
                {
                    'name': 'order_id',
                    'in': location,
                    'schema': {'type': 'string'},
                }
            )
            self.assertEqual(param.style, style)

        with self.assertRaises(pydantic.ValidationError):
            models.Parameter.model_validate(
                {
                    'name': 'order_id',
                    'in': 'cookie',
                    'style': 'response',
                    'schema': {'type': 'string'},
                }
            )

    def test_validator_robustness(self) -> None:
        value = object()
        self.assertIs(
            models.Parameter.set_defaults_based_on_parameter_location(value),  # type: ignore[operator]
            value,
        )
