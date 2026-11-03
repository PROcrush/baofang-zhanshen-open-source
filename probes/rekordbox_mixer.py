"""Optional read-only A/B crossfader observation for one verified code profile.

Caller supplies the already identity-checked RekordboxExperimentalReader. This
routine never acquires a task port or controls playback. Unavailable or changing
mixer state returns None independently of the deck-position capture.

Static source: original rekordbox 7.2.19.0342 arm64 UUID
D827BD18-6918-357A-9F57-C506CF428700, __text SHA256
1416f9eaa826e9aba138a176e997e9016a8fe360b0a06cf42b8ea4d5d9287445.
getMixerState 0x1027cd7c4: graph +0x468 array/+0x474 count, unit+0x188
state interface. isInternalMode 0x102aaad80: unit+0x2d0. Mixer input vector
is +0x2d8/+0x2e0. Input getCrossFaderAssign 0x102a91c7c uses +0x488;
CrossFaderAssign::getAssign 0x102abcbb8 reads applied+0x20; processBlock
0x102abc9f0 copies requested+0x24 after A(0)/B(1)/THRU(2) routing.
Input getCrossFaderPosition 0x102a91d2c uses +0x4d8/getParameter(0),
which reads f32+0x30 at 0x102abe79c, then multiplies by float 1/1023.
Each input allocates its own volume object at 0x102a90464; the mixer setter
0x102aa8d9c forwards the same normalized fader to every input. Thus object
addresses differ while candidate raw values must agree. Position 0 favors
CF_A/left, 1 favors CF_B/right; this is an A/B subtitle selection convention,
not proof that no third/fourth deck, THRU route, or other vocal is audible.
"""
import math
import struct

from probes.rekordbox_reader import ReaderError


_MIXER_VTABLE = 0x5350648
_MIXER_STATE_VTABLE = 0x5350E18
_INPUT_VTABLE = 0x534E8C0
_ASSIGN_VTABLE = 0x5351DE8
_VOLUME_VTABLE = 0x5351E70


def _snapshot(reader):
    roots = reader._roots()
    graph = roots[1]
    read, pointer = reader.native.read, reader._pointer
    mixer_count = struct.unpack("<I", read(graph + 0x474, 4))[0]
    if not 1 <= mixer_count <= 8:
        raise ValueError("mixer count")
    mixers = pointer(graph + 0x468)
    mixer = pointer(mixers)
    if (pointer(mixer) != reader.base + _MIXER_VTABLE
            or pointer(mixer + 0x188) != reader.base + _MIXER_STATE_VTABLE):
        raise ValueError("mixer class")
    if read(mixer + 0x2D0, 1) != b"\x01":
        raise ValueError("external or unknown mixer mode")
    begin, end = pointer(mixer + 0x2D8), pointer(mixer + 0x2E0)
    span = end - begin
    if span % 8 or not 2 <= span // 8 <= 8:
        raise ValueError("input vector bounds")
    identities, assigns, values = [], [], []
    for index in range(2):
        channel = pointer(begin + index * 8)
        if pointer(channel) != reader.base + _INPUT_VTABLE:
            raise ValueError("input class")
        assign, volume = pointer(channel + 0x488), pointer(channel + 0x4D8)
        if (pointer(assign) != reader.base + _ASSIGN_VTABLE
                or pointer(volume) != reader.base + _VOLUME_VTABLE):
            raise ValueError("crossfader class")
        assignment = read(assign + 0x20, 8)
        applied, requested = struct.unpack("<II", assignment)
        if applied != requested or applied not in (0, 1):
            raise ValueError("THRU, unavailable or changing assignment")
        value_bytes = read(volume + 0x30, 4)
        value = struct.unpack("<f", value_bytes)[0]
        if not math.isfinite(value) or not 0 <= value <= 1023:
            raise ValueError("crossfader bounds")
        identities.append((channel, assign, volume, assignment, value_bytes))
        assigns.append(applied)
        values.append(value)
    if identities[0][0] == identities[1][0] or identities[0][1] == identities[1][1]:
        raise ValueError("aliased A/B channels")
    if identities[0][2] == identities[1][2]:
        raise ValueError("unexpected shared volume object")
    if assigns[0] == assigns[1] or values[0] != values[1]:
        raise ValueError("same-side or unsynchronized A/B fader")
    if roots != reader._roots():
        raise ValueError("changed audio graph")
    return roots, mixers, mixer_count, mixer, begin, end, tuple(identities), tuple(assigns), values[0]


def read_mixer(reader):
    """Return stable normalized position and left/right A/B mapping, or None.

Only mixer inputs 0 and 1 participate in this rule. This function cannot
establish that the mix contains only those two decks or identify a singer.
"""
    try:
        before = _snapshot(reader)
        if before != _snapshot(reader):
            return None
        left = "A" if before[-2][0] == 0 else "B"
        return {"normalized": before[-1] / 1023.0, "leftSource": left,
                "rightSource": "B" if left == "A" else "A"}
    except (ReaderError, OSError, ValueError, TypeError, AttributeError, struct.error, OverflowError):
        return None
