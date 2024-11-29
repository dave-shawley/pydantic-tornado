import unittest

import pydantictornado


class VersionNumberTests(unittest.TestCase):
    def test_version_number(self) -> None:
        self.assertEqual(pydantictornado.__version__, '0.0.0')
        self.assertEqual(pydantictornado.version, pydantictornado.__version__)
