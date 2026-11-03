"""An independent, offline parser for the observed Serato Movie snapshot.

Provenance: the project's saved _movie_snapshot_parse/_children assembly and
the 49-byte dispatch table at VM 0x1010a28ea in the user's saved sample.
NativeMovieReader.frame(from:) at 0x100e439c4..0x100e43a84 and MovieDeck.init at
0x100e2dc7c establish the scalar names. The consumer treats position as seconds;
needle_raw and song_length_raw units remain unknown. This is an observed private
schema, not a claim of an official Serato API or compatibility.

No process, file, IPC, or network access occurs here. Parsing bytes does not
validate a producer, a stable shared-memory snapshot, or real playback meaning.
"""

from __future__ import annotations

import math
import struct
from typing import Iterator


BUFFER_SIZE = 0x1F400
MAGIC = 0x005E7A70
MAX_WORDS = 0x7D00
END = 0xFFFFFFFF
MAX_RECORDS = 191  # The observed parser rejects reaching 192 non-end records.
KNOWN_PARENT_TYPES = frozenset((0x100, 0x300, 0x400))
COMPLETE_MASK = 0x7F

# Field kind, output key, presence bit. Names come from the bounded frame
# conversion, not from the numerical ordering of the tags.
FIELD_SCHEMA = {
    0x105: ("uint32", "deck_index", 0x01),
    0x111: ("double", "needle_raw", 0x02),
    0x112: ("double", "velocity", 0x04),
    0x113: ("double", "position_seconds", 0x08),
    0x115: ("double", "song_length_raw", 0x10),
    0x134: ("utf16", "audio_path", 0x20),
    0x135: ("utf16", "video_path", 0x40),
}


class MovieParseError(ValueError):
    """Invalid input, with a stable reason code and byte offset."""

    def __init__(self, code: str, offset: int, detail: str):
        self.code = code
        self.offset = offset
        super().__init__(f"{code} at byte {offset}: {detail}")


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _records(data: bytes, start: int, end: int) -> Iterator[tuple[int, int, int, int]]:
    """Walk one bounded list; every length is checked before payload access."""
    cursor = start
    count = 0
    while True:
        if end - cursor < 4:
            raise MovieParseError("missing_end", cursor, "record list has no terminal word")
        tag = _u32(data, cursor)
        if tag == END:
            if cursor + 4 != end:
                raise MovieParseError("early_end", cursor, "terminal word must end this record list")
            return
        if count >= MAX_RECORDS:
            raise MovieParseError("record_limit", cursor, "too many non-terminal records")
        if end - cursor < 12:
            raise MovieParseError("short_header", cursor, "record header exceeds its container")
        words = _u32(data, cursor + 4)
        if words < 4 or words > (end - cursor) // 4:
            raise MovieParseError("record_length", cursor + 4, "word length exceeds its container or is below four")
        record_end = cursor + words * 4
        yield tag, cursor, record_end, words
        cursor = record_end
        count += 1


def _path(data: bytes, start: int, end: int, words: int) -> tuple[str | None, dict]:
    if words != 0x103 or _u32(data, end - 4) != 0x107:
        raise MovieParseError("string_type", start, "string requires 259 words and trailer 0x107")
    # The observed record reserves 1024 payload bytes but scans at most 256
    # UTF-16 code units. Do not scan the rest for a convenient late terminator.
    units = struct.unpack_from("<256H", data, start + 8)
    try:
        count = units.index(0)
    except ValueError as exc:
        raise MovieParseError("unterminated_string", start + 8, "no terminator within 256 code units") from exc
    try:
        text = data[start + 8 : start + 8 + count * 2].decode("utf-16-le", errors="strict")
    except UnicodeDecodeError as exc:
        raise MovieParseError("invalid_utf16", start + 8, "invalid UTF-16 sequence") from exc
    at_capacity = count == 255
    # The reference consumer receives an at-capacity flag. Its complete text
    # policy is not established; do not expose a potentially truncated path as
    # an address suitable for opening a song or submitting a paid task.
    status = "capacity_reached" if at_capacity else "ok" if text else "empty"
    return (text if status == "ok" else None), {
        "code_units": count,
        "at_capacity": at_capacity,
        "status": status,
    }


def _movie(data: bytes, start: int, end: int) -> dict:
    result = {"source_identity": _u32(data, start + 8), "presence_mask": 0, "unknown_tags": []}
    for kind, name, _bit in FIELD_SCHEMA.values():
        result[name] = None
    for tag, child_start, child_end, words in _records(data, start + 12, end):
        schema = FIELD_SCHEMA.get(tag)
        if schema is None:
            result["unknown_tags"].append(tag)
            continue
        kind, name, bit = schema
        if result["presence_mask"] & bit:
            raise MovieParseError("duplicate_field", child_start, f"repeated recognized tag 0x{tag:x}")
        trailer = _u32(data, child_end - 4)
        if kind == "uint32":
            if words != 4 or trailer != 0x103:
                raise MovieParseError("integer_type", child_start, "deck index requires four words and trailer 0x103")
            value = _u32(data, child_start + 8)
            if value not in (0, 1):
                raise MovieParseError("deck_index", child_start + 8, "deck index must be zero or one")
        elif kind == "double":
            if words != 5 or trailer != 0x101:
                raise MovieParseError("double_type", child_start, "scalar requires five words and trailer 0x101")
            value = struct.unpack_from("<d", data, child_start + 8)[0]
            if not math.isfinite(value):
                raise MovieParseError("nonfinite_double", child_start + 8, "NaN and infinity are invalid")
        else:
            value, text_status = _path(data, child_start, child_end, words)
            result[name + "_info"] = text_status
        result[name] = value
        result["presence_mask"] |= bit
    if result["deck_index"] is None:
        raise MovieParseError("missing_deck_index", start, "Movie requires tag 0x105")
    result["complete"] = result["presence_mask"] == COMPLETE_MASK
    return result


def parse_movie_snapshot(snapshot: bytes) -> dict:
    """Parse one immutable offline snapshot, or raise MovieParseError.

    Exact buffer size and observed schema are required. Empty or partial Movie
    lists can be structurally valid; ``complete`` requires both deck indices and
    all seven recognized fields. The function returns no fader or capture time.
    """
    if not isinstance(snapshot, bytes):
        raise TypeError("snapshot must be immutable bytes")
    if len(snapshot) != BUFFER_SIZE:
        raise MovieParseError("buffer_size", 0, f"expected exactly {BUFFER_SIZE} bytes")
    magic, words, reserved = struct.unpack_from("<III", snapshot)
    if magic != MAGIC:
        raise MovieParseError("magic", 0, "unsupported Movie magic")
    if reserved != 0:
        raise MovieParseError("reserved", 8, "reserved header must be zero")
    if not 4 <= words <= MAX_WORDS:
        raise MovieParseError("word_count", 4, "active word count must be 4..32000")
    identities = set()
    indices = set()
    movies = []
    unknown_parent_types = []
    for tag, start, end, _words in _records(snapshot, 12, words * 4):
        if tag not in KNOWN_PARENT_TYPES:
            unknown_parent_types.append(tag)
            continue
        identity = _u32(snapshot, start + 8)
        if identity == 0:
            raise MovieParseError("zero_identity", start + 8, "known parent identity cannot be zero")
        if identity in identities:
            raise MovieParseError("duplicate_identity", start + 8, "known parent identities must be distinct")
        identities.add(identity)
        if tag != 0x100:
            continue  # Routing/fader parents are not Movie field containers.
        if len(movies) >= 2:
            raise MovieParseError("movie_count", start, "at most two Movie records are supported")
        movie = _movie(snapshot, start, end)
        if movie["deck_index"] in indices:
            raise MovieParseError("duplicate_deck_index", start, "Movie deck indices must be distinct")
        indices.add(movie["deck_index"])
        movies.append(movie)
    movies.sort(key=lambda item: item["deck_index"])
    return {
        "schema_version": 1,
        "schema": "observed-serato-movie-little-endian-v1",
        "word_count": words,
        "active_bytes": words * 4,
        "structurally_valid": True,
        "complete": len(movies) == 2 and all(movie["complete"] for movie in movies),
        "movies": movies,
        "unknown_parent_types": unknown_parent_types,
        "live_connection_verified": False,
        "units": {"position_seconds": "seconds (consumer convention)", "velocity": "consumer playback velocity; live behavior unverified", "needle_raw": "unknown", "song_length_raw": "unknown"},
    }
