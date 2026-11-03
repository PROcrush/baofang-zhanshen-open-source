"""Behavioral tests for diagnostics; all external commands are mocked."""

import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

from probes import serato_probe as probe


def command_result(stdout="", stderr="", returncode=0, error=None):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "error": error}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / "Applications"
        self.apps.mkdir()
        self.ipc = self.root / "ipc"
        self.ipc.mkdir()
        self.bundle = self.apps / "Serato DJ Pro.app"
        self.main = self.create_bundle(self.bundle, "Serato DJ Pro", "com.serato.dj")
        self.video_bundle = self.bundle / "Contents" / "MacOS" / "Serato Video.app"
        self.video = self.create_bundle(self.video_bundle, "Serato Video", "com.serato.video")
        self.uid = os.getuid() if hasattr(os, "getuid") else 501
        self.marker = self.ipc / f"sslv_sharing{self.uid}"
        self.marker.touch()
        self.key = 0x52123456

    def create_bundle(self, path, name, identifier):
        (path / "Contents" / "MacOS").mkdir(parents=True)
        with (path / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({
                "CFBundleExecutable": name,
                "CFBundleIdentifier": identifier,
                "CFBundleShortVersionString": "4.0.5",
                "CFBundleVersion": "4.0.5.223",
            }, stream)
        executable = path / "Contents" / "MacOS" / name
        executable.write_bytes(b"mock executable; never launched")
        return executable

    def run_result(self, command):
        if command == probe.PS_COMMAND:
            return command_result(
                f" 123 {self.uid} {self.main}\n"
                f" 124 {self.uid} {self.video}\n"
                f" 125 {self.uid} {self.bundle}/Contents/MacOS/crashpad_handler\n"
                f" 126 {self.uid} /unrelated/Serato DJ Pro Helper\n"
            )
        if command == probe.SHM_COMMAND:
            owner = probe.pwd.getpwuid(self.uid).pw_name if probe.pwd else str(self.uid)
            return command_result(
                "T ID KEY MODE OWNER GROUP CPID LPID\nShared Memory:\n"
                f"m 123456 0x{self.key:08x} --rw-r--r-- {owner} staff 124 124\n"
            )
        if command == probe.SEM_COMMAND:
            owner = probe.pwd.getpwuid(self.uid).pw_name if probe.pwd else str(self.uid)
            return command_result(
                "T ID KEY MODE OWNER GROUP\nSemaphores:\n"
                f"s 654321 0x{self.key:08x} --rw-r--r-- {owner} staff\n"
            )
        if command[0] == "/usr/bin/codesign":
            if "--display" in command:
                return command_result(stderr="Identifier=com.serato.mock\nSignature=adhoc\nTeamIdentifier=not set\n")
            if "-R" in command:
                return command_result(stderr="code failed to satisfy specified code requirement(s)", returncode=1)
            return command_result()
        raise AssertionError(f"Unexpected command: {command}")

    def inspect(self, run=None):
        with mock.patch.object(probe.platform, "system", return_value="Darwin"), \
             mock.patch.object(probe.platform, "machine", return_value="arm64"), \
             mock.patch.object(probe.platform, "mac_ver", return_value=("15.5", ("", "", ""), "")), \
             mock.patch.object(probe, "APP_SEARCH_ROOTS", (self.apps,)), \
             mock.patch.object(probe, "IPC_ROOT", self.ipc), \
             mock.patch.object(probe, "_ftok_key", return_value=(self.key, None)), \
             mock.patch.object(probe, "_run", side_effect=run or self.run_result):
            return probe.inspect_serato()

    def test_inventory_never_claims_connection_or_field_support(self):
        report = self.inspect()
        self.assertEqual(len(report["processes"]["main"]), 1)
        self.assertEqual(len(report["processes"]["video"]), 1)
        self.assertEqual(len(report["applications"]), 2)
        self.assertEqual(report["applications"][0]["version"], "4.0.5")
        self.assertEqual(report["ipc"]["markers"][0]["shared_memory"][0]["creator_pid"], 124)
        self.assertTrue(report["ipc"]["markers"][0]["ipc_owner_matches"])
        self.assertTrue(report["ipc"]["markers"][0]["creator_matches_current_video"])
        self.assertFalse(report["ipc"]["connection_verified"])
        self.assertFalse(report["ipc"]["shared_memory_attached"])
        self.assertFalse(report["processes"]["main"][0]["identity_verified"])
        self.assertTrue(all(item["status"] == "unavailable" for item in report["capabilities"].values()))
        self.assertIn("本清单不证明实时连接", report["summary"])
        json.dumps(report)

    def test_ad_hoc_signature_is_warning_even_when_self_verify_passes(self):
        report = self.inspect()
        self.assertTrue(all(item["strict"]["passed"] for item in report["signatures"]))
        self.assertTrue(all(not item["apple_generic_anchor"]["passed"] for item in report["signatures"]))
        self.assertTrue(all(not item["official_compatibility_verified"] for item in report["signatures"]))
        checks = [item for item in report["checks"] if item["label"].startswith("磁盘签名")]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(item["status"] == "warning" for item in checks))

    def test_denied_process_and_ipc_commands_remain_unverified(self):
        def denied(command):
            if command in (probe.PS_COMMAND, probe.SHM_COMMAND, probe.SEM_COMMAND):
                # Even a partial stdout must not override the denial.
                return command_result(stdout=self.run_result(command)["stdout"], stderr="Operation not permitted", returncode=1)
            return self.run_result(command)
        report = self.inspect(denied)
        self.assertEqual(report["processes"]["status"], "unavailable")
        self.assertEqual(report["processes"]["main"], [])
        self.assertEqual(report["ipc"]["shared_memory_listing"]["status"], "unavailable")
        self.assertEqual(report["ipc"]["shared_memory_listing"]["entries"], [])
        self.assertIn("未完整核验", report["summary"])
        checks = {item["label"]: item for item in report["checks"]}
        self.assertEqual(checks["Serato 主进程"]["status"], "unavailable")
        self.assertIn("Operation not permitted", checks["共享段清单"]["detail"])

    def test_valid_empty_process_list_means_not_observed(self):
        def empty(command):
            return command_result() if command == probe.PS_COMMAND else self.run_result(command)
        report = self.inspect(empty)
        self.assertEqual(report["processes"]["status"], "ok")
        self.assertIn("未发现 Serato 主进程", report["summary"])
        checks = {item["label"]: item for item in report["checks"]}
        self.assertEqual(checks["Serato 主进程"]["status"], "warning")

    def test_malformed_process_list_does_not_prove_absence(self):
        inventory = probe._process_inventory(command_result(stdout="permission wrapper output\n"))
        self.assertEqual(inventory["status"], "unavailable")
        self.assertIn("无法解析", inventory["error"])

    def test_marker_symlink_is_rejected_before_ftok(self):
        self.marker.unlink()
        target = self.ipc / "target"
        target.touch()
        self.marker.symlink_to(target)
        with mock.patch.object(probe, "IPC_ROOT", self.ipc), \
             mock.patch.object(probe, "_run", side_effect=self.run_result), \
             mock.patch.object(probe, "_ftok_key") as key:
            ipc = probe._ipc_inventory({"main": [], "video": []})
        self.assertEqual(ipc["markers"][0]["status"], "error")
        self.assertFalse(ipc["markers"][0]["regular_file"])
        key.assert_not_called()

    def test_wrong_marker_owner_is_rejected(self):
        wrong_uid = self.uid + 1000
        (self.ipc / f"sslv_sharing{wrong_uid}").touch()
        with mock.patch.object(probe, "IPC_ROOT", self.ipc), \
             mock.patch.object(probe.os, "getuid", return_value=wrong_uid), \
             mock.patch.object(probe, "_run", side_effect=self.run_result), \
             mock.patch.object(probe, "_ftok_key") as key:
            ipc = probe._ipc_inventory({"main": [], "video": []})
        self.assertEqual(ipc["markers"][0]["status"], "error")
        self.assertFalse(ipc["markers"][0]["owner_matches"])
        key.assert_not_called()

    def test_invalid_plist_is_reported_without_crashing(self):
        (self.bundle / "Contents" / "Info.plist").write_bytes(b"invalid plist")
        report = self.inspect()
        self.assertEqual(report["applications"][0]["status"], "unavailable")

    def test_application_directory_permission_denial_is_preserved(self):
        with mock.patch.object(probe, "APP_SEARCH_ROOTS", (self.apps,)), \
             mock.patch.object(probe.os, "scandir", side_effect=PermissionError("directory denied")):
            applications, errors = probe._find_applications({"main": [], "video": []})
        self.assertEqual(applications, [])
        self.assertIn("directory denied", errors[0])

    def test_video_version_is_preserved_without_executable_plist_field(self):
        path = self.video_bundle / "Contents" / "Info.plist"
        info = plistlib.loads(path.read_bytes())
        del info["CFBundleExecutable"]
        path.write_bytes(plistlib.dumps(info))
        report = self.inspect()
        video = report["applications"][1]
        self.assertEqual(video["status"], "warning")
        self.assertEqual(video["version"], "4.0.5")
        self.assertEqual(len(report["signatures"]), 2)  # The observed process provides its path.

    def test_marker_creator_mismatch_warns_without_deciding_connection_validity(self):
        def mismatch(command):
            result = self.run_result(command)
            if command == probe.SHM_COMMAND:
                result["stdout"] = result["stdout"].replace("staff 124 124", "staff 999 124")
            return result
        report = self.inspect(mismatch)
        self.assertFalse(report["ipc"]["markers"][0]["creator_matches_current_video"])
        self.assertFalse(report["ipc"]["connection_verified"])
        check = next(item for item in report["checks"] if item["label"] == "IPC 生产者关系")
        self.assertEqual(check["status"], "warning")

    def test_malformed_ipc_row_is_unavailable(self):
        listing = probe._ipc_listing(command_result(stdout="m 1 invalid --rw-r--r-- user staff\n"), "m")
        self.assertEqual(listing["status"], "unavailable")

    def test_no_metadata_identifier_does_not_pass_signature_check(self):
        with mock.patch.object(probe, "_run", return_value=command_result()):
            signature = probe._signature(str(self.main))
        self.assertEqual(signature["metadata_status"], "unavailable")
        self.assertEqual(probe._signature_check(signature)["status"], "unavailable")

    def test_timeout_and_missing_command_are_not_valid_empty_results(self):
        with mock.patch.object(probe.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ps"], 3)):
            result = probe._run(probe.PS_COMMAND)
        self.assertIsNone(result["returncode"])
        self.assertIn("超时", result["error"])
        self.assertEqual(probe._process_inventory(result)["status"], "unavailable")
        with mock.patch.object(probe.subprocess, "run", side_effect=PermissionError("denied")):
            result = probe._run(probe.PS_COMMAND)
        self.assertEqual(result["error"], "denied")

    def test_non_macos_performs_no_external_inspection(self):
        with mock.patch.object(probe.platform, "system", return_value="Windows"), \
             mock.patch.object(probe, "_run") as run:
            report = probe.inspect_serato()
        run.assert_not_called()
        self.assertIn("尚未实现", report["summary"])
        self.assertFalse(report["ipc"]["connection_verified"])

    def test_cli_saves_same_json_that_it_prints(self):
        report = self.inspect()
        output = self.root / "reports" / "probe.json"
        captured = io.StringIO()
        with mock.patch.object(probe, "inspect_serato", return_value=report), contextlib.redirect_stdout(captured):
            result = probe.main(["--output", str(output)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(captured.getvalue()), json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
