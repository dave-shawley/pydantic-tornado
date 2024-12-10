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
