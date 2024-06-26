import asyncio
import datetime
import functools
import ipaddress
import re
import typing
import unittest.mock
import uuid

from pydantictornado import errors, routing, util
from tornado import web


class RouteTests(unittest.TestCase):
    def test_that_at_least_one_handler_must_be_included(self) -> None:
        with self.assertRaises(errors.NoHttpMethodsDefinedError) as cm:
            routing.Route('/')
        self.assertIn('HTTP method implementation', str(cm.exception))

        implementations = {
            n.lower() for n in web.RequestHandler.SUPPORTED_METHODS
        }
        for impl in implementations:
            try:
                routing.Route('/', **{impl: asyncio.sleep})
            except Exception:  # noqa: BLE001 -- blind except ok due to fail()
                self.fail(f'routing.Route failed though {impl!r} was defined')

    def test_that_pattern_is_anchored_if_string(self) -> None:
        r = routing.Route('/', get=asyncio.sleep)
        self.assertEqual(r'/$', r.regex.pattern)

    def test_that_regex_pattern_is_unchanged(self) -> None:
        r = routing.Route(re.compile('/'), get=asyncio.sleep)
        self.assertEqual(r'/', r.regex.pattern)

    def test_that_unprocessed_kwargs_are_passed_through(self) -> None:
        sentinel = object()
        r = routing.Route('/', get=asyncio.sleep, other=sentinel)
        self.assertEqual(sentinel, r.target_kwargs['other'])

    def test_that_path_parameters_are_processed(self) -> None:
        async def float_param(delay: float) -> float:
            return delay

        r = routing.Route(r'/(?P<delay>(?=0\.)?[0-9]+)', get=float_param)
        self.assertIn('path_types', r.target_kwargs)
        try:
            path_types = r.target_kwargs['path_types']
        except KeyError:
            self.fail("'path_types' missing from target_kwargs")

        try:
            coercion = path_types['delay']
        except KeyError:
            self.fail("'delay' parameter is missing from path_types")
        else:
            self.assertIsInstance(
                coercion('1.0'), float, 'invalid coercion function'
            )

    def test_that_noncoros_are_rejected(self) -> None:
        with self.assertRaises(errors.CoroutineRequiredError):
            routing.Route(r'/', get=print)

    def test_that_unconvertible_types_are_rejected(self) -> None:
        async def object_param(_obj: object) -> None:
            pass

        with self.assertRaises(errors.UnroutableParameterTypeError):
            routing.Route('r/(?P<_obj>.*)', get=object_param)

    def test_that_simple_types_are_recognized(self) -> None:
        now = datetime.datetime.now(datetime.UTC)
        expectations: dict[type, tuple[str, object]] = {
            int: ('10', 10),
            float: ('0.99', 0.99),
            str: ('a string', 'a string'),
            uuid.UUID: (str(uuid.UUID(int=0)), uuid.UUID(int=0)),
            datetime.date: (now.date().isoformat(), now.date()),
            datetime.datetime: (now.isoformat(), now),
            ipaddress.IPv4Address: (
                '127.0.0.1',
                ipaddress.IPv4Address('127.0.0.1'),
            ),
            ipaddress.IPv6Address: (
                'fe80::50ea:b0f2:bc3c:40e2%utun3',
                ipaddress.IPv6Address('fe80::50ea:b0f2:bc3c:40e2%utun3'),
            ),
        }
        for cls, (str_value, obj_value) in expectations.items():

            async def impl(*, _obj: cls) -> None:  # type: ignore[valid-type]
                pass

            r = routing.Route(r'/(?P<_obj>.*)', get=impl)
            result = r.target_kwargs['path_types']['_obj'](str_value)
            self.assertTrue(
                issubclass(type(result), cls),
                f'parsing {str_value!r} produced incompatible'
                f' type {type(result)}',
            )
            self.assertEqual(
                obj_value,
                result,
                f'parsing {str_value!r} produced unexpected value',
            )

    def test_parsing_bool(self) -> None:
        async def impl(*, _flag: bool) -> None:
            pass

        r = routing.Route(r'/(?P<_flag>.*)', get=impl)
        coercion = r.target_kwargs['path_types']['_flag']
        self.assertEqual(True, coercion('1'))
        self.assertEqual(False, coercion('0'))
        self.assertEqual(True, coercion('2'))

        for str_value in ('', 'true', 'yes', 'false', 'no'):
            with self.assertRaises(
                errors.ValueParseError,
                msg='_convert_bool should have failed for {str_value!r',
            ):
                coercion(str_value)

    def test_custom_boolean_strings(self) -> None:
        async def impl(*, _flag: bool) -> None:
            pass

        r = routing.Route(r'/(?P<_flag>.*)', get=impl)
        coercion = r.target_kwargs['path_types']['_flag']

        true_strings: set[str] = {'yes', 'true'}
        false_strings: set[str] = {'no', 'false'}
        with (
            unittest.mock.patch.object(
                util, 'BOOLEAN_TRUE_STRINGS', new=true_strings
            ),
            unittest.mock.patch.object(
                util, 'BOOLEAN_FALSE_STRINGS', new=false_strings
            ),
        ):
            for t_value in true_strings:
                self.assertIs(
                    True,  # noqa: FBT003, positional bool ok
                    coercion(t_value),
                    f'Custom conversion failed for {t_value!r}',
                )
            for f_value in false_strings:
                self.assertIs(
                    False,  # noqa: FBT003, positional bool ok
                    coercion(f_value),
                    f'Custom conversion failed for {f_value!r}',
                )

    def test_various_isoformat_formats(self) -> None:
        async def impl(*, _when: datetime.datetime) -> None:
            pass

        expectations = {
            '1992': datetime.datetime(1992, 1, 1, tzinfo=(datetime.UTC)),
            '1992-07': datetime.datetime(1992, 7, 1, tzinfo=(datetime.UTC)),
            '199207': datetime.datetime(1992, 7, 1, tzinfo=(datetime.UTC)),
            '19920401': datetime.datetime(1992, 4, 1, tzinfo=(datetime.UTC)),
            '1997-08-27': datetime.datetime(
                1997, 8, 27, tzinfo=(datetime.UTC)
            ),
            '1969-07-21 02:56+0000': datetime.datetime(
                1969, 7, 21, 2, 56, tzinfo=(datetime.UTC)
            ),
            '1969-07-21T02:56Z': datetime.datetime(
                1969, 7, 21, 2, 56, tzinfo=(datetime.UTC)
            ),
            '1969-07-21T02:56+00:00': datetime.datetime(
                1969, 7, 21, 2, 56, tzinfo=(datetime.UTC)
            ),
            '19690721T025632.123456+0000': datetime.datetime(
                1969, 7, 21, 2, 56, 32, 123456, tzinfo=(datetime.UTC)
            ),
        }

        route = routing.Route(r'/(?P<_when>.,*)', get=impl)
        coercion_func = route.target_kwargs['path_types']['_when']

        for str_value, obj_value in expectations.items():
            result = coercion_func(str_value)
            self.assertEqual(
                obj_value, result, f'Coercion failed for {str_value}'
            )

        for str_value in ('12/31/1999', ''):
            with self.assertRaises(
                errors.ValueParseError,
                msg=f'_parse_datetime should have failed for {str_value!r}',
            ):
                coercion_func(str_value)

    def test_parsing_union_types(self) -> None:
        async def impl(id_: int | uuid.UUID) -> str:
            return str(id_)

        route = routing.Route(r'/(?P<id_>.*)', get=impl)
        coercion_func = route.target_kwargs['path_types']['id_']
        self.assertIsNotNone(coercion_func)

        u = uuid.uuid4()
        expectations = {'12345': 12345, str(u): u}
        for str_value, expected in expectations.items():
            self.assertEqual(expected, coercion_func(str_value))

        with self.assertRaises(errors.ValueParseError):
            coercion_func(1.23)

    def test_incongruent_parameter_types(self) -> None:
        async def impl(*, _id: int | uuid.UUID) -> None:
            pass

        async def another_impl(*, _id: int | uuid.UUID) -> None:
            pass

        async def str_impl(*, _id: str) -> None:
            pass

        with self.assertRaises(errors.PathTypeMismatchError):
            routing.Route(r'/(?P<_id>.*)', get=str_impl, delete=impl)

        # should not raise
        routing.Route(r'/(?P<_id>.*)', get=impl, delete=another_impl)


class UUIDWrapperTests(unittest.TestCase):
    def test_that_construction_alternatives_are_supported(self) -> None:
        value = uuid.uuid4()
        for alternative in ('hex', 'bytes', 'bytes_le', 'fields', 'int'):
            self.assertEqual(
                value,
                routing._UUID(**{alternative: getattr(value, alternative)}),
            )

    def test_that_uuid_copying_is_supported(self) -> None:
        value = uuid.uuid4()
        self.assertEqual(value, routing._UUID(value))

    def test_that_uuid_can_be_annotated(self) -> None:
        uuid_type = typing.Annotated[routing._UUID, 'whatever']
        self.assertEqual(uuid.UUID(int=0), uuid_type(int=0))

    def test_that_uuid_is_still_immutable(self) -> None:
        uuid_type = typing.Annotated[routing._UUID, 'whatever']
        uuid = uuid_type(int=0)
        with self.assertRaises(TypeError):
            uuid.version = 4  # type: ignore[misc]


class EdgeCaseTests(unittest.TestCase):
    def test_path_converter_equality(self) -> None:
        converter = routing._PathConverter(int, routing.ParameterAnnotation())
        self.assertEqual(converter, converter)
        self.assertEqual(
            converter,
            routing._PathConverter(int, routing.ParameterAnnotation()),
        )
        self.assertNotEqual(
            converter,
            routing._PathConverter(float, routing.ParameterAnnotation()),
        )
        self.assertEqual(converter, converter.clone())

    def test_union_converter_equality(self) -> None:
        int_converter = routing._PathConverter(
            int, routing.ParameterAnnotation()
        )
        uuid_converter = routing._PathConverter(
            uuid.UUID, routing.ParameterAnnotation()
        )
        union_parameter = routing._UnionPathConverter(
            [int_converter, uuid_converter]
        )
        self.assertEqual(union_parameter, union_parameter)
        self.assertEqual(union_parameter, union_parameter.clone())

    def test_compare(self) -> None:
        self.assertTrue(
            routing._compare(functools.partial(int), functools.partial(int))
        )
        self.assertFalse(
            routing._compare(
                functools.partial(int, base=10), functools.partial(int)
            )
        )
        self.assertTrue(routing._compare(1, 1.0))
