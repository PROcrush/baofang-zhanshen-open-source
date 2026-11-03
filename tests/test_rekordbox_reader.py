"""Synthetic native boundary tests; these do not attach to rekordbox."""
import ctypes
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid

from probes import rekordbox_reader as rb


def macho(code=b"synthetic code", image_uuid=rb.ARM64_UUID):
    section = struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0x100001000,
                          len(code), 4096, 2, 0, 0, 0, 0, 0, 0)
    segment = struct.pack("<II16sQQQQiiII", 0x19, 152, b"__TEXT", 0x100000000,
                          8192, 0, 8192, 5, 5, 1, 0) + section
    commands = segment + struct.pack("<II16s", 0x1B, 24, uuid.UUID(image_uuid).bytes)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 2, len(commands), 0, 0)
    return (header + commands).ljust(4096, b"\0") + code


class FakeTask:
    def __init__(self, binary, data):
        self.memory, self.bytes_read, self.closed, self.reads = {}, 0, 0, 0
        self.on_read = None
        self.put(0x200000000, struct.pack("<IIQ", 17, 1, 0x200001000))
        self.put(0x200001000, struct.pack("<QQQ", 0x100000000, 0x200002000, 0))
        self.put(0x200002000, str(binary).encode().ljust(4096, b"\0"))
        self.put(0x100000000, data)
        self.ptr(0x1056C7AA0, 0x210000000)
        self.ptr(0x2100000C8, 0x220000000)
        self.ptr(0x220000458, 0x230000000)
        self.put(0x220000464, struct.pack("<I", 4))
        self.decks = []
        for index in range(2):
            start = 0x240000000 + index * 0x10000
            control, state, raw, loader, source, path, stretch = [start + j * 0x1000 for j in range(7)]
            self.decks.append((control, state, raw, loader, source, path, stretch))
            wrapper = start + 0x8000
            self.ptr(0x230000000 + index * 8, wrapper)
            self.ptr(wrapper + 0x40, control)
            self.ptr(control + 0xF8, state)
            self.ptr(state + 8, raw)
            self.ptr(state + 0x18, loader)
            self.ptr(loader + 0x70, source)
            self.ptr(source + 0x18, path)
            self.put(path, f"/音乐/Deck-{index}.mp3".encode().ljust(4096, b"\0"))
            self.put(raw + 8, bytes([index]))
            self.put(raw + 0xC, struct.pack("<i", 44100 * (index + 1)))
            self.ptr(raw + 0x70, stretch)
            self.put(stretch + 0xE4, struct.pack("<f", .06))

    def put(self, address, data):
        self.memory.update({address + i: b for i, b in enumerate(data)})

    def ptr(self, address, value):
        self.put(address, struct.pack("<Q", value))

    def read(self, address, size):
        self.reads += 1
        if self.on_read:
            self.on_read(address, size)
        try:
            result = bytes(self.memory[address + i] for i in range(size))
        except KeyError:
            raise rb.ReaderError("memory_unavailable") from None
        self.bytes_read += size
        return result

    def dyld_address(self):
        return 0x200000000

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed += 1


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve() / "test.app"
        self.binary = self.app / "Contents/MacOS/rekordbox"
        self.binary.parent.mkdir(parents=True)
        self.data = macho()
        self.binary.write_bytes(self.data)
        info = {"CFBundleIdentifier": "com.pioneerdj.rekordboxdj", "CFBundleExecutable": "rekordbox",
                "CFBundleShortVersionString": rb.VERSION, "CFBundleVersion": rb.VERSION}
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.patch = patch.object(rb, "TEXT_SHA256", hashlib.sha256(b"synthetic code").hexdigest())
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.native = FakeTask(self.binary, self.data)
        self.factory = Mock(return_value=self.native)

    def reader(self, path_for_pid=None):
        return rb.RekordboxExperimentalReader(self.app, native_factory=self.factory,
                                             path_for_pid=path_for_pid or (lambda _: self.binary),
                                             find_pid=lambda *_: 42)

    def test_two_decks_units_and_contract_remain_experimental(self):
        with self.reader() as reader:
            frame = reader.read_frame()
        self.assertEqual(frame["type"], "rekordbox_experimental_frame")
        self.assertFalse(frame["layoutRuntimeValidated"])
        self.assertFalse(frame["liveConnectionVerified"])
        self.assertFalse(frame["controllerCompatible"])
        self.assertNotIn("validUntilNs", frame)
        self.assertEqual([d["positionSeconds"] for d in frame["decks"]], [1, 2])
        self.assertNotEqual(*[d["trackPath"] for d in frame["decks"]])
        self.assertEqual([d["playRequested"] for d in frame["decks"]], [False, True])
        self.assertTrue(all(d["playing"] is None and d["speed"] is None for d in frame["decks"]))
        self.assertAlmostEqual(frame["decks"][0]["normalRateCandidate"], 1.06)
        self.assertEqual(self.native.closed, 1)

    def test_denied_task_port_never_reads_memory_or_leaks_right(self):
        lib = SimpleNamespace(**{name: Mock() for name in ("mach_task_self", "task_for_pid",
            "mach_port_deallocate", "mach_error_string", "task_info", "mach_vm_read_overwrite")})
        lib.mach_task_self.return_value, lib.task_for_pid.return_value = 1, 5
        with self.assertRaisesRegex(rb.ReaderError, "task_port_denied"):
            with rb.MachReadOnlyTask(42, library=lib):
                self.fail("denied task entered")
        lib.mach_vm_read_overwrite.assert_not_called()
        lib.mach_port_deallocate.assert_not_called()

    def test_wrong_disk_uuid_hash_or_process_identity_rejected_before_task(self):
        for change in ("uuid", "hash", "process", "version"):
            with self.subTest(change=change):
                self.binary.write_bytes(self.data)
                self.factory.reset_mock()
                if change == "uuid":
                    self.binary.write_bytes(macho(image_uuid="00000000-0000-0000-0000-000000000000"))
                if change == "hash":
                    self.binary.write_bytes(macho(code=b"changed code!!"))
                if change == "version":
                    info_path = self.app / "Contents/Info.plist"
                    info = plistlib.loads(info_path.read_bytes())
                    info["CFBundleVersion"] = "7.2.18"
                    info_path.write_bytes(plistlib.dumps(info))
                path = (lambda _: Path("/wrong/rekordbox")) if change == "process" else None
                with self.assertRaises(rb.ReaderError):
                    with self.reader(path):
                        self.fail("identity accepted")
                self.factory.assert_not_called()

    def test_mapped_uuid_mismatch_releases_task(self):
        self.native.put(0x100000000, macho(image_uuid="00000000-0000-0000-0000-000000000000"))
        with self.assertRaisesRegex(rb.ReaderError, "mapped_image_layout_mismatch"):
            with self.reader():
                self.fail("mapped UUID accepted")
        self.assertEqual(self.native.closed, 1)

    def test_empty_deck_returns_only_null_candidates(self):
        self.native.ptr(self.native.decks[0][3] + 0x70, 0)
        with self.reader() as reader:
            deck = reader.read_frame()["decks"][0]
        self.assertEqual(deck["status"], "empty_unvalidated")
        self.assertTrue(all(deck[k] is None for k in ("trackPath", "positionSamples", "positionSeconds",
                                                     "playRequested", "tempoDelta", "normalRateCandidate")))

    def test_track_change_and_unterminated_path_fail_closed(self):
        with self.reader() as reader:
            raw = self.native.decks[0][2]
            loader = self.native.decks[0][3]
            def switch(address, _size):
                if address == raw + 0xC:
                    self.native.ptr(loader + 0x70, 0)
            self.native.on_read = switch
            frame = reader.read_frame()
            self.assertEqual(frame["decks"][0]["error"]["code"], "track_identity_changed")
            self.assertIsNone(frame["decks"][0]["positionSeconds"])
            self.assertIsNotNone(frame["decks"][1]["positionSeconds"])
            self.native.on_read = None
            self.native.put(self.native.decks[1][5], b"x" * rb.MAX_PATH)
            deck = reader.read_frame()["decks"][1]
            self.assertEqual(deck["error"]["code"], "path_not_terminated")
            self.assertIsNone(deck["trackPath"])

    def test_wrapper_replacement_with_same_control_is_rejected(self):
        with self.reader() as reader:
            control, _, raw, *_ = self.native.decks[0]
            def replace_wrapper(address, _size):
                if address == raw + 0xC:
                    self.native.ptr(0x230000000, 0x260000000)
                    self.native.ptr(0x260000040, control)
            self.native.on_read = replace_wrapper
            frame = reader.read_frame()
            self.assertEqual(frame["decks"][0]["error"]["code"], "track_identity_changed")
            self.assertIsNone(frame["decks"][0]["trackPath"])
            self.assertEqual(frame["decks"][1]["status"], "observed_unvalidated")

    def test_cli_failure_preserves_bytes_read_during_enter(self):
        reader = MagicMock()
        reader.native.bytes_read = 384
        reader.__enter__.side_effect = rb.ReaderError("mapped_image_layout_mismatch")
        output = Path(self.temp.name) / "failure.jsonl"
        with patch.object(rb, "RekordboxExperimentalReader", return_value=reader), \
                patch.object(rb, "_output_path", return_value=output), patch("builtins.print"):
            self.assertEqual(rb.main([]), 2)
        frame = json.loads(output.read_text())
        self.assertEqual(frame["memoryBytesRead"], 384)
        self.assertTrue(all(d["positionSeconds"] is None for d in frame["decks"]))
        self.assertFalse(frame["liveConnectionVerified"])

    def test_cli_zero_exit_requires_an_observed_candidate(self):
        for status, exit_code in (("empty_unvalidated", 2), ("unavailable", 2),
                                  ("observed_unvalidated", 0)):
            with self.subTest(status=status):
                frame = rb.unavailable_frame(rb.ReaderError("layout_not_live_validated"))
                frame["decks"] = [rb._empty_deck(side, status) for side in "AB"]
                reader = MagicMock()
                reader.__enter__.return_value = reader
                reader.read_frame.return_value = frame
                output = Path(self.temp.name) / f"{status}.jsonl"
                with patch.object(rb, "RekordboxExperimentalReader", return_value=reader), \
                        patch.object(rb, "_output_path", return_value=output), patch("builtins.print"):
                    self.assertEqual(rb.main([]), exit_code)
                self.assertEqual(reader.read_frame.call_count, 1)


if __name__ == "__main__":
    unittest.main()
