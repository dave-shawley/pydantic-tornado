import collections.abc
import datetime
import typing
import unittest.mock

import pydantic
from pydantictornado import errors, util


class ClassMappingTests(unittest.TestCase):
    def test_subclass_handling(self) -> None:
        # fmt: off
        class A: ...
        class B(A): ...
        class C: ...
        class D(A, C): ...
        # fmt: on

        mapping = util.ClassMapping[str]()
        mapping[A] = 'A'
        self.assertEqual(mapping[A], 'A')
        self.assertEqual(mapping[B], 'A')
        self.assertEqual(mapping[D], 'A')
        with self.assertRaises(KeyError):
            mapping[C]

        mapping[B] = 'B'
        self.assertEqual(mapping[A], 'A')
        self.assertEqual(mapping[B], 'B')
        self.assertEqual(mapping[D], 'A')

        mapping[C] = 'C'
        self.assertEqual(mapping[A], 'A')
        self.assertEqual(mapping[B], 'B')
        self.assertEqual(mapping[C], 'C')
        self.assertEqual(mapping[D], 'A')

        mapping[D] = 'D'
        self.assertEqual(mapping[A], 'A')
        self.assertEqual(mapping[B], 'B')
        self.assertEqual(mapping[C], 'C')
        self.assertEqual(mapping[D], 'D')

    def test_collection_features(self) -> None:
        mapping = util.ClassMapping[str]()
        mapping[bool] = 'bool'
        mapping[int] = 'int'
        self.assertEqual(mapping[bool], 'bool')
        self.assertEqual(mapping[int], 'int')

        del mapping[bool]
        self.assertEqual(mapping[bool], 'int')
        with self.assertRaises(IndexError):
            del mapping[bool]

        self.assertIsNone(mapping.get(float, None))

        self.assertEqual(len(mapping), 1)
        self.assertListEqual([int], list(mapping))

    def test_that_non_types_fail(self) -> None:
        mapping = util.ClassMapping[str]()

        # we need a value in the cache to test for the subclass
        # failure case in mapping.get()
        mapping[str] = 'string'

        for value in (1, 1.0, 'value', (1, 2), [1]):
            with self.assertRaises(errors.TypeRequiredError):
                mapping[value] = str(value)  # type: ignore[index]
            with self.assertRaises(
                errors.TypeRequiredError, msg=f'not raised for {value!r}'
            ):
                mapping.get(value)  # type: ignore[call-overload]

    def test_that_clear_resets_cache(self) -> None:
        mapping = util.ClassMapping[str]()
        mapping[int] = 'int'
        mapping[bool] = 'bool'
        mapping[float] = 'float'
        mapping.populate_cache()
        self.assertEqual(len(mapping._cache), 3)

        mapping.clear()
        self.assertEqual(len(mapping._cache), 0)

    def test_rebuild(self) -> None:
        def initializer(m: collections.abc.MutableMapping[type, str]) -> None:
            m.update({int: 'int', str: 'str', bool: 'bool'})

        mapping = util.ClassMapping[str]()
        self.assertEqual(len(mapping), 0)
        initializer(mapping)
        self.assertEqual(len(mapping), 3)
        mapping.rebuild()
        self.assertEqual(len(mapping), 0)

        mapping = util.ClassMapping[str](initialize_data=initializer)
        self.assertEqual(len(mapping), 3)
        self.assertEqual(len(mapping._cache), 3)

        mapping.clear()
        self.assertEqual(len(mapping), 0)
        self.assertEqual(len(mapping._cache), 0)

        mapping[float] = 'float'
        self.assertEqual(len(mapping), 1)
        self.assertEqual(len(mapping._cache), 1)

        mapping.rebuild()
        self.assertEqual(len(mapping), 3)
        self.assertEqual(len(mapping._cache), 3)

    def test_that_none_is_handled(self) -> None:
        none_type = type(None)
        mapping = util.ClassMapping[str]()
        mapping[none_type] = 'null'
        self.assertEqual('null', mapping[none_type])


class JSONSerializationTests(unittest.TestCase):
    def test_with_unhandled_type(self) -> None:
        with self.assertRaisesRegex(TypeError, r'object is not serializable'):
            util.json_serialize_hook(object())

    def test_timedelta_serialization(self) -> None:
        expectations = [
            ('PT0S', datetime.timedelta()),
            (
                'P1DT2H3M4.567S',
                datetime.timedelta(
                    days=1, hours=2, minutes=3, seconds=4, milliseconds=567
                ),
            ),
            ('PT12H', datetime.timedelta(days=0.5)),
            ('PT0.5S', datetime.timedelta(seconds=0.5)),
            ('PT0.123S', datetime.timedelta(milliseconds=123)),
            ('PT0.123456S', datetime.timedelta(microseconds=123456)),
        ]
        for expected, value in expectations:
            self.assertEqual(
                expected,
                util.json_serialize_hook(value),
                f'Unexpected serialization for {value!r}',
            )

    def test_pydantic_serialization(self) -> None:
        class Widget(pydantic.BaseModel):
            name: str
            slug: str

        serialized = util.json_serialize_hook(
            Widget(name='Something Useful', slug='something-useful')
        )
        self.assertDictEqual(
            {'name': 'Something Useful', 'slug': 'something-useful'},
            typing.cast(dict[object, object], serialized),
        )

    def test_pydantic_none_field_omission(self) -> None:
        class Widget(util.FieldOmittingMixin, pydantic.BaseModel):
            OMIT_IF_NONE = ('description',)
            name: str
            slug: str
            description: str | None = None

        serialized = util.json_serialize_hook(
            Widget(name='Something Useful', slug='something-useful')
        )
        self.assertDictEqual(
            {'name': 'Something Useful', 'slug': 'something-useful'},
            typing.cast(dict[object, object], serialized),
        )

        serialized = util.json_serialize_hook(
            Widget(
                name='Something Useful',
                slug='something-useful',
                description='',
            )
        )
        self.assertDictEqual(
            {
                'name': 'Something Useful',
                'slug': 'something-useful',
                'description': '',
            },
            typing.cast(dict[object, object], serialized),
        )

    def test_pydantic_field_omission_with_aliases(self) -> None:
        class Widget(util.FieldOmittingMixin, pydantic.BaseModel):
            OMIT_IF_NONE = ('description',)
            id_: int = pydantic.Field(alias='id')
            name: str
            slug: str
            description: str | None = None

        serialized = util.json_serialize_hook(
            Widget(id=1, name='Something Useful', slug='something-useful')
        )
        self.assertDictEqual(
            {'id': 1, 'name': 'Something Useful', 'slug': 'something-useful'},
            typing.cast(dict[object, object], serialized),
        )


class UtilityFunctionTests(unittest.TestCase):
    def test_apply_default_of_unspecified(self) -> None:
        self.assertIsNone(util.apply_default(None, util.UNSPECIFIED))
        self.assertIs(
            util.UNSPECIFIED,
            util.apply_default(util.UNSPECIFIED, util.UNSPECIFIED),
        )
        self.assertEqual(
            unittest.mock.sentinel.value,
            util.apply_default(
                unittest.mock.sentinel.value, unittest.mock.sentinel.default
            ),
        )
        self.assertEqual(
            unittest.mock.sentinel.value,
            util.apply_default(
                unittest.mock.sentinel.value, util.Unspecified()
            ),
        )

    def test_apply_default_of_none(self) -> None:
        self.assertEqual(
            unittest.mock.sentinel.value,
            util.apply_default(unittest.mock.sentinel.value, None),
        )

    def test_apply_default_of_ellipsis(self) -> None:
        self.assertEqual(
            unittest.mock.sentinel.value,
            util.apply_default(unittest.mock.sentinel.value, ...),
        )

    def test_apply_default_value_unspecified(self) -> None:
        self.assertEqual(
            unittest.mock.sentinel.default,
            util.apply_default(
                util.UNSPECIFIED, unittest.mock.sentinel.default
            ),
        )

    def test_apply_default_value_none(self) -> None:
        self.assertEqual(
            unittest.mock.sentinel.default,
            util.apply_default(None, unittest.mock.sentinel.default),
        )
