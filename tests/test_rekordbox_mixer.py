"""Mixer observations use a synthetic byte boundary; no application access."""
import struct
import unittest

from probes.rekordbox_mixer import read_mixer
from probes.rekordbox_reader import ReaderError


class FakeReader:
    base = 0x100000000

    def __init__(self):
        self.native = self
        self.memory, self.on_read = {}, None
        self.roots = (0x200000000, 0x210000000, 0x220000000, 4)
        self.mixers, self.mixer, self.inputs = 0x230000000, 0x240000000, 0x250000000
        self.put(self.roots[1] + 0x474, struct.pack("<I", 1))
        self.ptr(self.roots[1] + 0x468, self.mixers)
        self.ptr(self.mixers, self.mixer)
        self.ptr(self.mixer, self.base + 0x5350648)
        self.ptr(self.mixer + 0x188, self.base + 0x5350E18)
        self.put(self.mixer + 0x2D0, b"\x01")
        self.ptr(self.mixer + 0x2D8, self.inputs)
        self.ptr(self.mixer + 0x2E0, self.inputs + 4 * 8)
        self.channels, self.assigns, self.volumes = [], [], []
        for index in range(2):
            channel, assign, volume = (0x260000000 + index * 0x10000 + offset
                                       for offset in (0, 0x1000, 0x2000))
            self.channels.append(channel)
            self.assigns.append(assign)
            self.volumes.append(volume)
            self.ptr(self.inputs + index * 8, channel)
            self.ptr(channel, self.base + 0x534E8C0)
            self.ptr(channel + 0x488, assign)
            self.ptr(channel + 0x4D8, volume)
            self.ptr(assign, self.base + 0x5351DE8)
            self.ptr(volume, self.base + 0x5351E70)
            self.assignment(index, index)
            self.put(volume + 0x30, struct.pack("<f", 511.5))

    def put(self, address, data):
        self.memory.update({address + i: b for i, b in enumerate(data)})

    def ptr(self, address, value):
        self.put(address, struct.pack("<Q", value))

    def assignment(self, index, applied, requested=None):
        self.put(self.assigns[index] + 0x20, struct.pack("<II", applied, applied if requested is None else requested))

    def read(self, address, size):
        if self.on_read:
            self.on_read(address, size)
        try:
            return bytes(self.memory[address + i] for i in range(size))
        except KeyError:
            raise ReaderError("memory_unavailable") from None

    def _pointer(self, address):
        value = struct.unpack("<Q", self.read(address, 8))[0]
        if not value or value % 8:
            raise ReaderError("invalid_pointer")
        return value

    def _roots(self):
        return self.roots


class MixerTests(unittest.TestCase):
    def test_forward_mapping_endpoints_and_middle(self):
        reader = FakeReader()
        for raw, normalized in ((0, 0), (511.5, .5), (1023, 1)):
            with self.subTest(raw=raw):
                for volume in reader.volumes:
                    reader.put(volume + 0x30, struct.pack("<f", raw))
                self.assertEqual(read_mixer(reader), {"normalized": normalized, "leftSource": "A", "rightSource": "B"})

    def test_reversed_assignments_reverse_sources_not_the_position(self):
        reader = FakeReader()
        reader.assignment(0, 1)
        reader.assignment(1, 0)
        self.assertEqual(read_mixer(reader), {"normalized": .5, "leftSource": "B", "rightSource": "A"})

    def test_thru_external_same_side_and_pending_assignment_rejected(self):
        for scenario in ("thru", "external", "same_side", "pending", "unsynchronized"):
            with self.subTest(scenario=scenario):
                reader = FakeReader()
                if scenario == "thru": reader.assignment(0, 2)
                if scenario == "external": reader.put(reader.mixer + 0x2D0, b"\x00")
                if scenario == "same_side": reader.assignment(1, 0)
                if scenario == "pending": reader.assignment(0, 0, 1)
                if scenario == "unsynchronized": reader.put(reader.volumes[1] + 0x30, struct.pack("<f", 100))
                self.assertIsNone(read_mixer(reader))

    def test_snapshot_changes_rejected_even_when_each_snapshot_is_valid(self):
        for scenario in ("value", "identity", "mode", "assignment"):
            with self.subTest(scenario=scenario):
                reader = FakeReader()
                reads = 0
                def change(address, size):
                    nonlocal reads
                    if address != reader.mixer + 0x2D0:
                        return
                    reads += 1
                    if reads != 2:
                        return
                    if scenario == "value":
                        for volume in reader.volumes:
                            reader.put(volume + 0x30, struct.pack("<f", 123))
                    if scenario == "identity":
                        new_volume = 0x270000000
                        reader.ptr(new_volume, reader.base + 0x5351E70)
                        reader.put(new_volume + 0x30, struct.pack("<f", 511.5))
                        reader.ptr(reader.channels[0] + 0x4D8, new_volume)
                    if scenario == "mode": reader.put(reader.mixer + 0x2D0, b"\x00")
                    if scenario == "assignment":
                        reader.assignment(0, 1)
                        reader.assignment(1, 0)
                reader.on_read = change
                self.assertIsNone(read_mixer(reader))

    def test_invalid_profile_bounds_or_read_errors_are_optional_unavailable(self):
        for scenario in ("vtable", "channel_count", "alias", "nan", "infinite", "range", "read_error"):
            with self.subTest(scenario=scenario):
                reader = FakeReader()
                if scenario == "vtable": reader.ptr(reader.mixer, reader.base + 0x1234)
                if scenario == "channel_count": reader.ptr(reader.mixer + 0x2E0, reader.inputs + 9 * 8)
                if scenario == "alias": reader.ptr(reader.channels[1] + 0x4D8, reader.volumes[0])
                if scenario in ("nan", "infinite", "range"):
                    value = {"nan": float("nan"), "infinite": float("inf"), "range": 1024}[scenario]
                    for volume in reader.volumes:
                        reader.put(volume + 0x30, struct.pack("<f", value))
                if scenario == "read_error": reader.memory.clear()
                self.assertIsNone(read_mixer(reader))


if __name__ == "__main__":
    unittest.main()
