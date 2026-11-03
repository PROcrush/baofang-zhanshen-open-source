"""Synthetic offline fixtures; no application, IPC, or real music required."""

import json
import math
import struct
import unittest

from probes.movie_parser import BUFFER_SIZE, END, MAGIC, MAX_RECORDS, MovieParseError, parse_movie_snapshot


def record(tag, payload):
    assert len(payload) % 4 == 0
    return struct.pack("<II", tag, (len(payload) + 8) // 4) + payload


def integer(tag, value, trailer=0x103):
    return record(tag, struct.pack("<II", value, trailer))


def scalar(tag, value, trailer=0x101):
    return record(tag, struct.pack("<dI", value, trailer))


def text(tag, value, trailer=0x107):
    encoded = value.encode("utf-16-le") + b"\0\0"
    assert len(encoded) <= 1024
    return record(tag, encoded.ljust(1024, b"\0") + struct.pack("<I", trailer))


def movie(index=0, identity=100, children=None):
    if children is None:
        children = [
            integer(0x105, index),
            scalar(0x111, 1234.5),
            scalar(0x112, 1.05),
            scalar(0x113, 42.75),
            scalar(0x115, 123456.0),
            text(0x134, "/音乐/往事随风-Remix.mp3"),
            text(0x135, "/视频/背景.mp4"),
        ]
    return record(0x100, struct.pack("<I", identity) + b"".join(children) + struct.pack("<I", END))


def snapshot(parents=()):
    payload = b"".join(parents) + struct.pack("<I", END)
    active = struct.pack("<III", MAGIC, (len(payload) + 12) // 4, 0) + payload
    assert len(active) <= BUFFER_SIZE
    return active.ljust(BUFFER_SIZE, b"\0")


def replace_u32(data, offset, value):
    result = bytearray(data)
    struct.pack_into("<I", result, offset, value)
    return bytes(result)


class MovieParserTests(unittest.TestCase):
    def assert_bad(self, data, code):
        with self.assertRaises(MovieParseError) as raised:
            parse_movie_snapshot(data)
        self.assertEqual(raised.exception.code, code)
        self.assertIsInstance(raised.exception.offset, int)

    def test_two_complete_movies_map_exact_tags_and_sort_by_index(self):
        report = parse_movie_snapshot(snapshot([movie(1, 200), movie(0, 100)]))
        self.assertTrue(report["structurally_valid"])
        self.assertTrue(report["complete"])
        self.assertFalse(report["live_connection_verified"])
        first, second = report["movies"]
        self.assertEqual((first["deck_index"], first["source_identity"]), (0, 100))
        self.assertEqual((second["deck_index"], second["source_identity"]), (1, 200))
        self.assertEqual(first["needle_raw"], 1234.5)
        self.assertEqual(first["velocity"], 1.05)
        self.assertEqual(first["position_seconds"], 42.75)
        self.assertEqual(first["song_length_raw"], 123456.0)
        self.assertEqual(first["audio_path"], "/音乐/往事随风-Remix.mp3")
        self.assertEqual(first["video_path"], "/视频/背景.mp4")
        self.assertEqual(first["presence_mask"], 0x7F)
        json.dumps(report, allow_nan=False)

    def test_zero_or_partial_movies_are_structural_only(self):
        self.assertFalse(parse_movie_snapshot(snapshot())["complete"])
        report = parse_movie_snapshot(snapshot([movie(children=[integer(0x105, 0)])]))
        self.assertFalse(report["complete"])
        self.assertEqual(report["movies"][0]["presence_mask"], 1)
        self.assertIsNone(report["movies"][0]["position_seconds"])

    def test_unknown_records_are_skipped_with_validated_lengths(self):
        unknown = record(0x999, b"abcdabcd")
        children = [integer(0x105, 0), unknown]
        report = parse_movie_snapshot(snapshot([unknown, movie(children=children)]))
        self.assertEqual(report["unknown_parent_types"], [0x999])
        self.assertEqual(report["movies"][0]["unknown_tags"], [0x999])

    def test_requires_exact_buffer_and_immutable_bytes(self):
        self.assert_bad(b"", "buffer_size")
        self.assert_bad(snapshot() + b"\0", "buffer_size")
        with self.assertRaises(TypeError):
            parse_movie_snapshot(bytearray(snapshot()))

    def test_header_magic_reserved_and_word_count(self):
        self.assert_bad(replace_u32(snapshot(), 0, 0), "magic")
        self.assert_bad(replace_u32(snapshot(), 8, 1), "reserved")
        for words in (0, 3, 32001, 0xFFFFFFFF):
            with self.subTest(words=words):
                self.assert_bad(replace_u32(snapshot(), 4, words), "word_count")

    def test_unused_buffer_tail_is_not_parsed_as_active_data(self):
        image = bytearray(snapshot())
        image[16:] = b"\xff" * (BUFFER_SIZE - 16)
        self.assertEqual(parse_movie_snapshot(bytes(image))["movies"], [])

    def test_parent_and_child_lengths_stay_inside_their_containers(self):
        data = snapshot([movie()])
        for words in (0, 3, 0xFFFFFFFF):
            with self.subTest(parent=words):
                self.assert_bad(replace_u32(data, 16, words), "record_length")
            with self.subTest(child=words):
                self.assert_bad(replace_u32(data, 28, words), "record_length")

    def test_early_end_and_missing_end_are_rejected(self):
        data = snapshot([movie()])
        self.assert_bad(replace_u32(data, 12, END), "early_end")
        self.assert_bad(replace_u32(data, 24, END), "early_end")
        self.assert_bad(replace_u32(snapshot(), 12, 0), "short_header")
        self.assert_bad(replace_u32(data, 4, 3 + (struct.unpack_from("<I", data, 16)[0])), "missing_end")

    def test_source_identity_is_nonzero_and_unique_across_known_parent_types(self):
        self.assert_bad(snapshot([movie(identity=0)]), "zero_identity")
        self.assert_bad(snapshot([movie(0, 100), movie(1, 100)]), "duplicate_identity")
        routing = record(0x300, struct.pack("<II", 100, END))
        self.assert_bad(snapshot([movie(identity=100), routing]), "duplicate_identity")

    def test_deck_indices_are_present_distinct_and_limited(self):
        self.assert_bad(snapshot([movie(children=[])]), "missing_deck_index")
        self.assert_bad(snapshot([movie(children=[integer(0x105, 2)])]), "deck_index")
        self.assert_bad(snapshot([movie(0, 100), movie(0, 200)]), "duplicate_deck_index")
        self.assert_bad(snapshot([movie(0, 100), movie(1, 200), movie(0, 300)]), "movie_count")

    def test_duplicate_recognized_tag_is_rejected(self):
        for duplicate in (integer(0x105, 0), scalar(0x113, 10), text(0x134, "x")):
            children = [integer(0x105, 0)] if struct.unpack_from("<I", duplicate)[0] != 0x105 else []
            self.assert_bad(snapshot([movie(children=children + [duplicate, duplicate])]), "duplicate_field")

    def test_scalar_lengths_and_trailers_are_checked(self):
        children = [integer(0x105, 0), scalar(0x113, 1.0, trailer=0x103)]
        self.assert_bad(snapshot([movie(children=children)]), "double_type")
        children = [integer(0x105, 0), integer(0x113, 1)]
        self.assert_bad(snapshot([movie(children=children)]), "double_type")
        self.assert_bad(snapshot([movie(children=[integer(0x105, 0, trailer=0x101)])]), "integer_type")

    def test_nonfinite_values_are_rejected_but_reverse_velocity_is_preserved(self):
        for number in (math.nan, math.inf, -math.inf):
            for tag in (0x111, 0x112, 0x113, 0x115):
                with self.subTest(number=number, tag=tag):
                    self.assert_bad(snapshot([movie(children=[integer(0x105, 0), scalar(tag, number)])]), "nonfinite_double")
        report = parse_movie_snapshot(snapshot([movie(children=[integer(0x105, 0), scalar(0x112, -1.0)])]))
        self.assertEqual(report["movies"][0]["velocity"], -1.0)

    def test_utf16_terminator_and_encoding_are_strict(self):
        children = [integer(0x105, 0), text(0x134, "/音乐/🎵.mp3")]
        self.assertEqual(parse_movie_snapshot(snapshot([movie(children=children)]))["movies"][0]["audio_path"], "/音乐/🎵.mp3")
        invalid = record(0x134, b"\0\xd8\0\0".ljust(1024, b"\0") + struct.pack("<I", 0x107))
        self.assert_bad(snapshot([movie(children=[integer(0x105, 0), invalid])]), "invalid_utf16")
        no_end = record(0x134, (b"a\0" * 256 + b"\0\0").ljust(1024, b"\0") + struct.pack("<I", 0x107))
        self.assert_bad(snapshot([movie(children=[integer(0x105, 0), no_end])]), "unterminated_string")

    def test_at_capacity_and_empty_paths_are_not_exposed_as_usable_addresses(self):
        children = [integer(0x105, 0), text(0x134, "a" * 255), text(0x135, "")]
        report = parse_movie_snapshot(snapshot([movie(children=children)]))["movies"][0]
        self.assertIsNone(report["audio_path"])
        self.assertEqual(report["audio_path_info"]["status"], "capacity_reached")
        self.assertEqual(report["audio_path_info"]["code_units"], 255)
        self.assertIsNone(report["video_path"])
        self.assertEqual(report["video_path_info"]["status"], "empty")

    def test_string_length_and_trailer_are_checked(self):
        children = [integer(0x105, 0), text(0x134, "x", trailer=0x101)]
        self.assert_bad(snapshot([movie(children=children)]), "string_type")
        children = [integer(0x105, 0), integer(0x134, 0)]
        self.assert_bad(snapshot([movie(children=children)]), "string_type")

    def test_top_and_child_record_budgets_prevent_unbounded_work(self):
        unknown = record(0x999, b"abcdabcd")
        parse_movie_snapshot(snapshot([unknown] * MAX_RECORDS))
        self.assert_bad(snapshot([unknown] * (MAX_RECORDS + 1)), "record_limit")
        children = [integer(0x105, 0)] + [unknown] * (MAX_RECORDS - 1)
        parse_movie_snapshot(snapshot([movie(children=children)]))
        self.assert_bad(snapshot([movie(children=children + [unknown])]), "record_limit")


if __name__ == "__main__":
    unittest.main()
