"""Fader graph tests use synthetic bytes; they never access live IPC."""

import math
import struct
import unittest

from probes.fader_parser import FaderParseError, parse_fader_snapshot
from test_movie_parser import END, integer, movie, record, scalar, snapshot


def route(identity, target):
    return record(0x300, struct.pack("<I", identity) + integer(0x301, target) + struct.pack("<I", END))


def fader(identity=500, left=100, right=200, children=None):
    children = children if children is not None else [integer(0x401, left), integer(0x402, right), scalar(0x420, .2), scalar(0x421, .3), scalar(0x422, .75)]
    return record(0x400, struct.pack("<I", identity) + b"".join(children) + struct.pack("<I", END))


class FaderParserTests(unittest.TestCase):
    def assert_bad(self, parents, code):
        with self.assertRaises(FaderParseError) as raised:
            parse_fader_snapshot(snapshot(parents))
        self.assertEqual(raised.exception.code, code)

    def test_normalized_is_tag422_and_independent_of_auxiliary_values(self):
        result = parse_fader_snapshot(snapshot([movie(0, 100), movie(1, 200), fader()]))
        self.assertEqual(result["normalized"], .75)
        self.assertEqual((result["tag420"], result["tag421"]), (.2, .3))
        self.assertEqual((result["left_deck_index"], result["right_deck_index"]), (0, 1))
        self.assertFalse(result["live_connection_verified"])

    def test_routing_chains_and_swapped_assignments(self):
        result = parse_fader_snapshot(snapshot([route(700, 800), movie(1, 200), fader(left=700, right=100), route(800, 200), movie(0, 100)]))
        self.assertEqual((result["left_deck_index"], result["right_deck_index"]), (1, 0))

    def test_cycles_unknown_references_zero_and_wrong_parent_types(self):
        base = [movie(0, 100), movie(1, 200)]
        self.assert_bad(base + [route(700, 800), route(800, 700), fader(left=700)], "routing_cycle")
        self.assert_bad(base + [fader(left=700)], "unknown_reference")
        self.assert_bad(base + [fader(left=0)], "unknown_reference")
        self.assert_bad(base + [fader(left=500)], "routing_type")

    def test_references_must_resolve_to_different_decks(self):
        self.assert_bad([movie(0, 100), movie(0, 200), fader()], "same_deck")
        self.assert_bad([movie(0, 100), route(700, 100), fader(left=700, right=100)], "same_deck")

    def test_missing_or_multiple_faders(self):
        base = [movie(0, 100), movie(1, 200)]
        self.assert_bad(base, "fader_count")
        self.assert_bad(base + [fader(), fader(identity=600)], "fader_count")

    def test_required_fields_duplicates_and_identity(self):
        base = [movie(0, 100), movie(1, 200)]
        self.assert_bad(base + [fader(children=[])], "missing_field")
        self.assert_bad(base + [fader(children=[integer(0x401, 100)] * 2)], "duplicate_field")
        self.assert_bad(base + [fader(identity=100)], "duplicate_identity")
        self.assert_bad(base + [fader(identity=0)], "zero_identity")
        self.assert_bad(base + [route(300, 200), record(0x300, struct.pack("<II", 700, END)), fader()], "missing_field")

    def test_all_fader_scalars_have_finite_closed_unit_interval(self):
        base = [movie(0, 100), movie(1, 200)]
        for tag in (0x420, 0x421, 0x422):
            for number in (-.001, 1.001, math.nan, math.inf, -math.inf):
                with self.subTest(tag=tag, number=number):
                    children = [integer(0x401, 100), integer(0x402, 200)] + [scalar(t, number if t == tag else .5) for t in (0x420, 0x421, 0x422)]
                    self.assert_bad(base + [fader(children=children)], "fader_range")
        for number in (0., 1.):
            children = [integer(0x401, 100), integer(0x402, 200)] + [scalar(t, number) for t in (0x420, 0x421, 0x422)]
            self.assertEqual(parse_fader_snapshot(snapshot(base + [fader(children=children)]))["normalized"], number)

    def test_scalar_and_reference_lengths_and_trailers(self):
        base = [movie(0, 100), movie(1, 200)]
        self.assert_bad(base + [fader(children=[integer(0x401, 100, trailer=0x101)])], "integer_type")
        self.assert_bad(base + [fader(children=[scalar(0x422, .5, trailer=0x103)])], "double_type")
        self.assert_bad(base + [fader(children=[integer(0x422, 0)])], "double_type")

    def test_unknown_records_are_bounded_but_do_not_fabricate_fields(self):
        unknown = record(0x999, b"abcdefgh")
        result = parse_fader_snapshot(snapshot([unknown, movie(0, 100), movie(1, 200), fader()]))
        self.assertEqual(result["unknown_parent_types"][0]["tag"], 0x999)
        self.assert_bad([record(0x100, struct.pack("<I", 100) + unknown + struct.pack("<I", END)), fader()], "missing_field")

    def test_immutable_size_and_header(self):
        with self.assertRaises(TypeError):
            parse_fader_snapshot(bytearray(snapshot()))
        with self.assertRaises(FaderParseError):
            parse_fader_snapshot(b"")
        data = bytearray(snapshot())
        struct.pack_into("<I", data, 4, 32001)
        with self.assertRaises(FaderParseError) as raised:
            parse_fader_snapshot(bytes(data))
        self.assertEqual(raised.exception.code, "word_count")


if __name__ == "__main__":
    unittest.main()
