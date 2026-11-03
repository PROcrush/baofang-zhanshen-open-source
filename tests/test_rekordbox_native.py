"""Native transport tests use tiny synthetic helper processes, never rekordbox."""
import hashlib
import json
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from probes import rekordbox_native as native
from probes.rekordbox_reader import ReaderError


class NativeTests(unittest.TestCase):
    def task(self, body, timeout=.5):
        script = "import sys,time\nprint('OK READY',flush=True)\n" + body
        children = []
        def spawn(_arguments, **options):
            child = subprocess.Popen([sys.executable, "-u", "-c", script], **options)
            children.append(child)
            return child
        task = native.NativeReadOnlyTask(42, timeout=timeout,
                                        _helper_builder=lambda _: Path("/synthetic/helper"), _spawn=spawn)
        return task, children

    def test_round_trip_exact_bytes_dyld_and_owned_process_cleanup(self):
        body = """for line in sys.stdin:
 if line=='Q\\n': break
 if line=='D\\n': print('OK D 0x200000000',flush=True)
 elif line=='R 1000 3\\n': print('OK R 3 0001ff',flush=True)
 else: print('ERR PROTOCOL 0',flush=True)
"""
        task, children = self.task(body)
        with task:
            self.assertEqual(task.dyld_address(), 0x200000000)
            self.assertEqual(task.read(0x1000, 3), b"\0\1\xff")
            self.assertEqual(task.bytes_read, 3)
        self.assertIsNotNone(children[0].poll())
        self.assertIsNone(task.process)

    def test_target_path_and_request_bounds_rejected(self):
        with self.assertRaisesRegex(ReaderError, "native_target_not_allowed"):
            native.NativeReadOnlyTask(42, appPath="/Applications/rekordbox 7/rekordbox.app")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            elsewhere = root / "elsewhere.app"
            elsewhere.mkdir()
            selected = root / "test.app"
            selected.symlink_to(elsewhere, target_is_directory=True)
            with patch.object(native, "DEFAULT_TEST_APP", selected), \
                    self.assertRaisesRegex(ReaderError, "native_target_not_allowed"):
                native._target(selected)
        task, children = self.task("for line in sys.stdin:\n if line=='Q\\n': break\n")
        with task:
            for address, size in ((0, 4), (0x1000, 0), (0x1000, 65537), (native.MAX_POINTER, 8)):
                with self.subTest(address=address, size=size), self.assertRaisesRegex(ReaderError, "read_bounds"):
                    task.read(address, size)
            self.assertEqual(task.bytes_read, 0)
        self.assertIsNotNone(children[0].poll())

    def test_read_failure_does_not_return_previous_bytes(self):
        body = """count=0
for line in sys.stdin:
 if line=='Q\\n': break
 count+=1
 print('OK R 2 aabb' if count==1 else 'ERR READ 1',flush=True)
"""
        task, _ = self.task(body)
        with task:
            self.assertEqual(task.read(0x1000, 2), b"\xaa\xbb")
            with self.assertRaisesRegex(ReaderError, "memory_unavailable"):
                task.read(0x1000, 2)
            self.assertEqual(task.bytes_read, 2)

    def test_bad_response_timeout_and_oversize_fail_closed(self):
        responses = ["print('OK R 2 00xx',flush=True)", "print('OK R 1 aa',flush=True)",
                     "print('x'*140000,flush=True)", "time.sleep(1)"]
        for response in responses:
            with self.subTest(response=response):
                task, children = self.task("for line in sys.stdin:\n " + response + "\n", timeout=.1)
                with task:
                    with self.assertRaises(ReaderError):
                        task.read(0x1000, 2)
                    self.assertIsNone(task.process)
                    self.assertEqual(task.bytes_read, 0)
                self.assertIsNotNone(children[0].poll())

    def test_helper_cache_requires_source_key_binary_digest_and_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper = Path(temporary) / "helper"
            manifest = Path(temporary) / "manifest.json"
            helper.write_bytes(b"signed test artifact")
            manifest.write_text(json.dumps({"buildKey": "source-and-target", "helperSha256": hashlib.sha256(helper.read_bytes()).hexdigest()}))
            with patch.object(native, "_signature_valid", return_value=True) as signature:
                self.assertTrue(native._cache_valid(helper, manifest, "source-and-target"))
                self.assertFalse(native._cache_valid(helper, manifest, "changed-source"))
                helper.write_bytes(b"changed bytes")
                self.assertFalse(native._cache_valid(helper, manifest, "source-and-target"))
                self.assertEqual(signature.call_count, 1)
            helper.write_bytes(b"signed test artifact")
            with patch.object(native, "_signature_valid", return_value=False):
                self.assertFalse(native._cache_valid(helper, manifest, "source-and-target"))

    def test_signature_requires_debugger_right_and_hardened_runtime(self):
        for rights, flags, expected in ((native.ENTITLEMENTS, b"flags=0x10002(adhoc,runtime)", True),
                                        (native.ENTITLEMENTS, b"flags=0x2(adhoc)", False),
                                        ({}, b"flags=0x10002(adhoc,runtime)", False)):
            with self.subTest(flags=flags, rights=rights):
                verify = subprocess.CompletedProcess([], 0, b"", b"")
                display = subprocess.CompletedProcess([], 0, plistlib.dumps(rights), flags)
                with patch.object(native, "_run", side_effect=[verify, display]):
                    self.assertEqual(native._signature_valid(Path("/synthetic/helper")), expected)


if __name__ == "__main__":
    unittest.main()
