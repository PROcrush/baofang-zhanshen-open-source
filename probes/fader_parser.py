"""Independent bounded parser for the observed Serato Video fader graph.

Uses saved fader-parse analysis, resolver VM 0x100fb740c, and frame conversion
to establish tags. Tag 0x422 is normalized; 0x420/0x421 have no established
physical unit. This module only interprets immutable offline bytes.
"""

from __future__ import annotations

import math
import struct

from .movie_parser import BUFFER_SIZE, MAGIC, MAX_WORDS, MovieParseError, _records, _u32


class FaderParseError(MovieParseError):
    """Invalid fader structure, with stable code and byte offset."""


def _integer(data: bytes, start: int, end: int, words: int) -> int:
    if words != 4 or _u32(data, end - 4) != 0x103:
        raise FaderParseError("integer_type", start, "reference/index requires four words and trailer 0x103")
    return _u32(data, start + 8)


def _scalar(data: bytes, start: int, end: int, words: int) -> float:
    if words != 5 or _u32(data, end - 4) != 0x101:
        raise FaderParseError("double_type", start, "fader scalar requires five words and trailer 0x101")
    value = struct.unpack_from("<d", data, start + 8)[0]
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise FaderParseError("fader_range", start + 8, "fader scalar must be finite and within 0..1")
    return value


def _resolve(objects: dict[int, dict], identity: int) -> int:
    visited = set()
    while True:
        if identity in visited:
            raise FaderParseError("routing_cycle", 0, "routing graph contains a cycle")
        visited.add(identity)
        item = objects.get(identity)
        if item is None:
            raise FaderParseError("unknown_reference", 0, "routing identity does not exist")
        if item["tag"] == 0x100:
            index = item["fields"][0x105]
            if index not in (0, 1):
                raise FaderParseError("deck_index", item["offset"], "resolved Movie index must be zero or one")
            return index
        if item["tag"] != 0x300:
            raise FaderParseError("routing_type", item["offset"], "routing must terminate at a Movie")
        identity = item["fields"][0x301]


def parse_fader_snapshot(snapshot: bytes) -> dict:
    """Parse exactly one fader and its routing graph, or raise FaderParseError.

    Both references must resolve to distinct zero-based Movie indices. All
    known parents require their observed fields; unknown fields are skipped
    only after their lengths have been checked. No producer is verified here.
    """
    if not isinstance(snapshot, bytes):
        raise TypeError("snapshot must be immutable bytes")
    if len(snapshot) != BUFFER_SIZE:
        raise FaderParseError("buffer_size", 0, f"expected exactly {BUFFER_SIZE} bytes")
    magic, words, reserved = struct.unpack_from("<III", snapshot)
    if magic != MAGIC:
        raise FaderParseError("magic", 0, "unsupported snapshot magic")
    if reserved:
        raise FaderParseError("reserved", 8, "reserved header must be zero")
    if not 4 <= words <= MAX_WORDS:
        raise FaderParseError("word_count", 4, "active word count must be 4..32000")
    objects = {}
    faders = []
    unknown_parents = []
    try:
        for tag, start, end, record_words in _records(snapshot, 12, words * 4):
            if tag not in (0x100, 0x300, 0x400):
                unknown_parents.append({"tag": tag, "offset": start, "words": record_words})
                continue
            identity = _u32(snapshot, start + 8)
            if identity == 0:
                raise FaderParseError("zero_identity", start + 8, "known identity cannot be zero")
            if identity in objects:
                raise FaderParseError("duplicate_identity", start + 8, "known identities must be unique")
            required = {0x105} if tag == 0x100 else {0x301} if tag == 0x300 else {0x401, 0x402, 0x420, 0x421, 0x422}
            item = {"tag": tag, "offset": start, "words": record_words, "fields": {}, "unknown_tags": []}
            for child_tag, child_start, child_end, child_words in _records(snapshot, start + 12, end):
                if child_tag not in required:
                    item["unknown_tags"].append(child_tag)
                    continue
                if child_tag in item["fields"]:
                    raise FaderParseError("duplicate_field", child_start, "repeated recognized child tag")
                reader = _scalar if tag == 0x400 and child_tag in (0x420, 0x421, 0x422) else _integer
                item["fields"][child_tag] = reader(snapshot, child_start, child_end, child_words)
            if item["fields"].keys() != required:
                raise FaderParseError("missing_field", start, "known parent lacks required child fields")
            objects[identity] = item
            if tag == 0x400:
                faders.append((identity, item))
    except MovieParseError as exc:
        if isinstance(exc, FaderParseError):
            raise
        raise FaderParseError(exc.code, exc.offset, str(exc)) from exc
    if len(faders) != 1:
        raise FaderParseError("fader_count", 12, "exactly one fader parent is required")
    identity, item = faders[0]
    fields = item["fields"]
    left = _resolve(objects, fields[0x401])
    right = _resolve(objects, fields[0x402])
    if left == right:
        raise FaderParseError("same_deck", item["offset"], "fader must resolve to two distinct Movie indices")
    return {
        "schema_version": 1,
        "complete": True,
        "word_count": words,
        "normalized": fields[0x422],
        "left_deck_index": left,
        "right_deck_index": right,
        "source_identity": identity,
        "left_source_reference": fields[0x401],
        "right_source_reference": fields[0x402],
        "tag420": fields[0x420],
        "tag421": fields[0x421],
        "objects": objects,
        "unknown_parent_types": unknown_parents,
        "live_connection_verified": False,
    }
