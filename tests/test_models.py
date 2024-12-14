import unittest

from pydantictornado import models


class C:
    pass


class MarkerTests(unittest.TestCase):
    def test_extract_with_non_marker(self) -> None:
        obj = C()
        setattr(obj, '__pydantic_tornado_method__', 42)  # noqa: B010
        with self.assertRaises(TypeError):
            models.OpenAPIMethodMarker.extract(obj)

    def test_that_frozen_marker_is_immutable(self) -> None:
        marker = models.FrozenMethodMarker()
        with self.assertRaises(TypeError):
            marker.response_type = None
        with self.assertRaises(AttributeError):
            marker.extra.clear()


class OpenAPILogicTests(unittest.TestCase):
    def test_parameter_path_property_initialization(self) -> None:
        param = models.Parameter.model_validate(
            {'name': 'page_size', 'in': 'query'}
        )
        self.assertIs(param.required, False)  # noqa: FBT003 -- positional bool param ok here

        param = models.Parameter.model_validate(
            {'name': 'order_id', 'in': 'path'}
        )
        self.assertIs(param.required, True)  # noqa: FBT003 -- positional bool param ok here

        param = models.Parameter.model_validate(
            {'name': 'page_size', 'in': 'query', 'required': True}
        )
        self.assertIs(param.required, True)  # noqa: FBT003 -- positional bool param ok here
