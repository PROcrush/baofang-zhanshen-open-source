"""Reader safety properties tested through an isolated fake native boundary."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from app.messages import validate_serato_frame
from app.serato_adapter import adapt_serato_frame
from probes.macos_ipc import BsdInfo, IpcPerm, MacReadOnlyIPC, SemInfo, ShmInfo
from probes.serato_reader import FRAME_TTL_NS, ReaderError, SeratoReader, _file_stamp, status_message
from test_fader_parser import fader
from test_movie_parser import integer, movie, scalar, snapshot, text


class FakeNative:
    def __init__(self, paths, data):
        self.processes = {pid: {"pid": pid, "uid": os.getuid(), "real_uid": os.getuid(), "started_seconds": 100 + pid, "started_microseconds": 1, "executable": str(path)} for pid, path in zip((10, 11), paths)}
        permission = {"uid": os.getuid(), "creator_uid": os.getuid(), "key": 123, "mode": 0o666, "sequence": 1}
        self.shm = {"permission": permission, "size": 128000, "creator_pid": 9, "ctime": 90}
        self.sem = {"permission": copy.deepcopy(permission), "count": 1, "ctime": 90}
        self.sem_value, self.sem_pid = 1, 11
        self.data = data, data
        self.attached = 0
        self.detached = 0
        self.on_copy = None
        self.identifiers = (7, 8)

    def process(self, pid):
        return copy.deepcopy(self.processes[pid])

    def marker_key(self, _path):
        return 123

    def ids(self, _key):
        return self.identifiers

    def shm_info(self, _identifier):
        return copy.deepcopy(self.shm)

    def sem_info(self, _identifier):
        return copy.deepcopy(self.sem)

    def semaphore(self, _identifier):
        return self.sem_value, self.sem_pid

    def attach(self, _identifier):
        self.attached += 1
        return 999

    def snapshots(self, _address):
        if self.on_copy:
            self.on_copy()
        return self.data

    def detach(self, _address):
        self.detached += 1


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.main_bundle = self.root / "Serato DJ Pro.app"
        self.video_bundle = self.main_bundle / "Contents" / "MacOS" / "Serato Video.app"
        self.paths = [self.main_bundle / "Contents" / "MacOS" / "Serato DJ Pro", self.video_bundle / "Contents" / "MacOS" / "Serato Video"]
        for path in self.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test artifact")
        self.marker = self.root / "marker"
        self.marker.touch()
        self.data = snapshot([movie(0, 100), movie(1, 200), fader()])
        self.native = FakeNative(self.paths, self.data)

    def tearDown(self):
        self.temporary.cleanup()

    def inventory(self):
        return {"status": "ok", "main": [{"pid": 10}], "video": [{"pid": 11}]}

    def artifact(self, executable, role):
        path = Path(executable)
        return {"path": executable, "bundle": str(self.main_bundle if role == "main" else self.video_bundle), "version": "test", "build": "1", "sha256": "test", "signature": {}, "files": [(executable, _file_stamp(path))]}

    def reader(self, **options):
        return SeratoReader(native=self.native, process_inventory=options.get("inventory", self.inventory), artifact_inspector=self.artifact, marker_path=self.marker)

    def assert_reader_error(self, reader, code, terminal=None):
        with self.assertRaises(ReaderError) as raised:
            reader.read_frame()
        self.assertEqual(raised.exception.code, code)
        if terminal is not None:
            self.assertEqual(raised.exception.terminal, terminal)

    def test_contract_and_sequence_ttl_use_system_monotonic_clock(self):
        with self.reader() as reader:
            with patch("probes.serato_reader._clock_ns", side_effect=[10**15, 10**15 + 100, 10**15 + 200]):
                frame = reader.read_frame()
            self.assertEqual(frame["type"], "serato_frame")
            self.assertEqual((frame["protocolVersion"], frame["mode"], frame["sequence"]), (1, "serato", 1))
            self.assertTrue(frame["producerVerified"])
            self.assertEqual(frame["validUntilNs"] - frame["capturedNs"], FRAME_TTL_NS)
            self.assertEqual(frame["decks"][0]["position_seconds"], 42.75)
            self.assertEqual(frame["fader"]["normalized"], .75)
            self.assertEqual(frame["diagnostics"]["clock"], "CLOCK_MONOTONIC")
            self.assertEqual(frame["diagnostics"]["producers"], list(self.native.processes.values()))
            self.assertFalse(frame["diagnostics"]["official_compatibility_verified"])
            self.assertEqual(reader.read_frame()["sequence"], 2)
            json.dumps(frame, allow_nan=False)
        self.assertEqual(self.native.detached, 1)

    def test_each_new_attachment_has_unique_epoch(self):
        with self.reader() as first:
            epoch = first.epoch
        with self.reader() as second:
            self.assertNotEqual(epoch, second.epoch)

    def test_single_loaded_deck_reaches_consumer_when_other_path_is_empty_or_at_capacity(self):
        # Real Serato emits an empty path for an unloaded deck. The parser also
        # withholds at-capacity paths; neither may invalidate the loaded deck.
        for path, status in (("", "empty"), ("x" * 255, "capacity_reached")):
            children = [integer(0x105, 1), scalar(0x111, 0), scalar(0x112, 0),
                        scalar(0x113, 0), scalar(0x115, 0), text(0x134, path), text(0x135, "")]
            data = snapshot([movie(0, 100), movie(1, 200, children=children), fader()])
            self.native.data = data, data
            with self.subTest(status=status), self.reader() as reader:
                frame = reader.read_frame()
                validate_serato_frame(frame)
                raw = adapt_serato_frame(frame, {})
                self.assertEqual(raw['A']['path'], '/音乐/往事随风-Remix.mp3')
                self.assertEqual(raw['A']['positionMs'], 42750)
                self.assertEqual(raw['B']['path'], '')
                self.assertFalse(raw['B']['identityReady'])
                self.assertEqual(frame['diagnostics']['audio_path_status']['1'], status)

    def test_old_creator_pid_is_not_mistaken_for_a_dead_writer(self):
        # Existing objects can be reused by a newly born Video. GETPID pins the
        # active sem writer; creator PID alone cannot establish current usage.
        with self.reader() as reader:
            self.assertTrue(reader.read_frame()["producerVerified"])
            self.assertEqual(reader.shm["creator_pid"], 9)

    def test_permission_denial_never_means_no_process(self):
        inventory = lambda: {"status": "unavailable", "error": "Operation not permitted", "main": [], "video": []}
        with self.assertRaises(ReaderError) as raised:
            self.reader(inventory=inventory)
        self.assertEqual(raised.exception.code, "process_unavailable")
        self.assertIn("Operation not permitted", raised.exception.detail)
        self.assertEqual(self.native.attached, 0)

    def test_wrong_owner_size_sem_count_or_writer_rejected_before_attach(self):
        for change in ("owner", "creator_owner", "size", "sem_count", "writer"):
            with self.subTest(change=change):
                self.native = FakeNative(self.paths, self.data)
                if change == "owner":
                    self.native.shm["permission"]["uid"] += 1
                elif change == "creator_owner":
                    self.native.sem["permission"]["creator_uid"] += 1
                elif change == "size":
                    self.native.shm["size"] += 1
                elif change == "sem_count":
                    self.native.sem["count"] = 2
                else:
                    self.native.sem_pid = 12345
                with self.assertRaises(ReaderError):
                    self.reader()
                self.assertEqual(self.native.attached, 0)

    def test_pid_reuse_and_ipc_lifecycle_changes_invalidate_and_detach(self):
        for change, code in (("birth", "process_restarted"), ("ids", "ipc_replaced"), ("shm_ctime", "ipc_lifecycle"), ("sem_ctime", "ipc_lifecycle"), ("sequence", "ipc_lifecycle")):
            with self.subTest(change=change):
                self.native = FakeNative(self.paths, self.data)
                with self.reader() as reader:
                    if change == "birth":
                        self.native.processes[11]["started_microseconds"] += 1
                    elif change == "ids":
                        self.native.identifiers = 70, 80
                    elif change == "shm_ctime":
                        self.native.shm["ctime"] += 1
                    elif change == "sem_ctime":
                        self.native.sem["ctime"] += 1
                    else:
                        self.native.shm["permission"]["sequence"] += 1
                    self.assert_reader_error(reader, code, True)
                    self.assertTrue(reader.invalidated)
                    self.assertIsNone(reader.address)
                self.assertEqual(self.native.detached, 1)

    def test_marker_and_artifact_replacement_invalidate(self):
        with self.reader() as reader:
            replacement = self.root / "replacement"
            replacement.touch()
            replacement.replace(self.marker)
            self.assert_reader_error(reader, "marker_changed", True)
        with self.reader() as reader:
            self.paths[0].write_bytes(b"changed binary")
            self.assert_reader_error(reader, "artifact_changed", True)

    def test_busy_and_writer_race_do_not_publish_a_previous_frame(self):
        with self.reader() as reader:
            reader.read_frame()
            self.native.sem_value = 0
            self.assert_reader_error(reader, "producer_busy", False)
            self.assertEqual(reader.sequence, 1)
            self.native.sem_value = 1
            self.native.on_copy = lambda: setattr(self.native, "sem_pid", 12345)
            self.assert_reader_error(reader, "semaphore_writer", True)

    def test_both_copies_must_have_identical_parsed_movies_and_fader(self):
        with self.reader() as reader:
            different = snapshot([movie(0, 100), movie(1, 200), fader(left=200, right=100)])
            self.native.data = self.data, different
            self.assert_reader_error(reader, "unstable_snapshot", False)
            self.assertEqual(reader.sequence, 0)

    def test_unrecognized_active_payload_changes_are_also_rejected(self):
        with self.reader() as reader:
            # A Movie string reserves more than the established 256-u16 scan.
            # Changing the unused string capacity leaves parsed fields equal.
            different = bytearray(self.data)
            first_text_offset = 12 + 12 + 16 + 4 * 20
            different[first_text_offset + 8 + 800] = 1
            self.native.data = self.data, bytes(different)
            self.assert_reader_error(reader, "unstable_payload", False)

    def test_invalid_partial_or_slow_snapshots_do_not_publish(self):
        with self.reader() as reader:
            self.native.data = b"", b""
            self.assert_reader_error(reader, "snapshot_schema", False)
            partial = snapshot([movie(0, 100, children=[integer(0x105, 0)]), movie(1, 200), fader()])
            self.native.data = partial, partial
            self.assert_reader_error(reader, "incomplete_movies", False)
            self.native.data = self.data, self.data
            with patch("probes.serato_reader._clock_ns", side_effect=[10**15, 10**15 + 100, 10**15 + 100_000_001]):
                self.assert_reader_error(reader, "capture_slow", False)

    def test_empty_path_and_reverse_velocity_are_truthful_fields(self):
        children = [integer(0x105, 0), scalar(0x111, 1), scalar(0x112, -1), scalar(0x113, -2), scalar(0x115, 3), text(0x134, ""), text(0x135, "")]
        data = snapshot([movie(0, 100, children=children), movie(1, 200), fader()])
        self.native.data = data, data
        with self.reader() as reader:
            frame = reader.read_frame()
            self.assertEqual(frame["decks"][0]["audio_path"], "")
            self.assertEqual(frame["decks"][0]["velocity"], -1)
            self.assertEqual(frame["diagnostics"]["audio_path_status"]["0"], "empty")

    def test_identity_failure_after_copies_invalidates_frame(self):
        with self.reader() as reader:
            self.native.on_copy = lambda: self.native.processes[10].update(started_seconds=999)
            self.assert_reader_error(reader, "process_restarted", True)
            self.assertEqual(reader.sequence, 0)

    def test_status_has_no_decks_and_no_claim_of_verified_producer(self):
        result = status_message(ReaderError("producer_busy", "等待生产者"))
        self.assertEqual((result["type"], result["status"]), ("serato_status", "waiting"))
        self.assertNotIn("decks", result)
        self.assertFalse(result["producerVerified"])

    def test_abi_matches_sdk_layout(self):
        import ctypes
        self.assertEqual((ctypes.sizeof(IpcPerm), ctypes.sizeof(ShmInfo), ShmInfo.ctime.offset), (24, 76, 60))
        self.assertEqual((ctypes.sizeof(SemInfo), SemInfo.ctime.offset), (72, 44))
        self.assertEqual((ctypes.sizeof(BsdInfo), BsdInfo.start_seconds.offset), (136, 120))

    def test_native_boundary_never_creates_and_always_attaches_read_only(self):
        native = MacReadOnlyIPC.__new__(MacReadOnlyIPC)
        native.lib = Mock()
        native.lib.shmget.return_value = 7
        native.lib.semget.return_value = 8
        native.lib.shmat.return_value = 999
        native.lib.shmdt.return_value = 0
        self.assertEqual(native.ids(123), (7, 8))
        native.lib.shmget.assert_called_once_with(123, 1, 0x100)
        native.lib.semget.assert_called_once_with(123, 1, 0x100)
        self.assertEqual(native.attach(7), 999)
        native.lib.shmat.assert_called_once_with(7, None, 0x1000)
        native.detach(999)
        native.lib.shmdt.assert_called_once_with(999)
        # Fixed read commands, no SETVAL/SETALL/semop even for busy producers.
        native.lib.semctl.side_effect = [0, 11]
        self.assertEqual(native.semaphore(8), (0, 11))
        self.assertEqual(native.lib.semctl.call_args_list, [call(8, 0, 5), call(8, 0, 4)])

    def test_two_full_copies_have_native_barriers(self):
        native = MacReadOnlyIPC.__new__(MacReadOnlyIPC)
        native.lib = Mock()
        with patch("probes.macos_ipc.C.string_at", side_effect=[self.data, self.data]) as read:
            self.assertEqual(native.snapshots(999), (self.data, self.data))
        self.assertEqual(read.call_args_list, [call(999, 128000), call(999, 128000)])
        self.assertEqual(native.lib.OSMemoryBarrier.call_count, 3)


if __name__ == "__main__":
    unittest.main()
