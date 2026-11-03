"""Portable bundle data paths and trusted bundled-resource boundaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import runtime_paths
from probes import rekordbox_ax
from scripts import repair_qt_metadata
from worker import studio_transcribe
from worker.studio_render import _usable_tool
from app.studio_tools import _ffmpeg_path


class PackagedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name).resolve()
        self.root = self.folder / '可搬移.app/Contents/Resources/app'
        self.root.mkdir(parents=True)
        self.marker = {'schemaVersion': 1, 'bundleIdentifier': runtime_paths.BUNDLE_IDENTIFIER}
        self.write_marker()

    def write_marker(self):
        (self.root / 'packaged.json').write_text(json.dumps(self.marker))

    def test_packaged_data_goes_to_user_library_and_development_stays_in_workspace(self):
        with patch('app.runtime_paths.Path.home', return_value=self.folder / 'user'):
            self.assertEqual(runtime_paths.default_runtime(self.root),
                             self.folder / 'user/Library/Application Support/包房战神')
        workspace = self.folder / 'workspace'
        workspace.mkdir()
        (workspace / 'packaged.json').write_text(json.dumps(self.marker))
        self.assertEqual(runtime_paths.default_runtime(workspace), workspace / 'runtime')
        self.assertFalse((self.root / 'runtime').exists())

    def test_missing_or_invalid_bundle_marker_fails_without_falling_back_to_bundle_writes(self):
        for value in ('{}', '{broken', 'null', json.dumps({**self.marker, 'schemaVersion': True})):
            (self.root / 'packaged.json').write_text(value)
            with self.assertRaises(RuntimeError):
                runtime_paths.default_runtime(self.root)
        (self.root / 'packaged.json').unlink()
        with self.assertRaises(RuntimeError):
            runtime_paths.default_runtime(self.root)

    def test_qt_metadata_repair_never_mutates_installed_bundle(self):
        with patch.object(repair_qt_metadata, '__file__', str(self.root / 'scripts/repair_qt_metadata.py')), \
                patch.object(repair_qt_metadata.os, 'chflags') as change:
            self.assertEqual(repair_qt_metadata.repair_qt_metadata(), 0)
            change.assert_not_called()

    def test_studio_startup_environment_prevents_bytecode_through_exec_chain(self):
        from app.studio import StudioController
        qt_environment = StudioController._environment(None)
        environment = {key: qt_environment.value(key) for key in qt_environment.keys()}
        module = self.folder / 'bundle_fixture.py'
        module.write_text('value = 42\n')
        launcher = 'import os,sys; os.setsid(); os.execv(sys.argv[1],sys.argv[1:])'
        code = ('import bundle_fixture,json,sys; '
                'print(json.dumps([bundle_fixture.value,sys.flags.dont_write_bytecode,sys.flags.no_user_site]))')
        result = subprocess.run([sys.executable, '-c', launcher, sys.executable, '-c', code],
                                cwd=self.folder, env=environment, capture_output=True, text=True,
                                check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout), [42, 1, 1])
        self.assertFalse((self.folder / '__pycache__').exists())

    def test_only_own_exact_tiny_model_is_allowed_inside_an_app(self):
        model = self.root / 'assets/models/tiny.pt'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'model fixture')
        self.assertEqual(runtime_paths.validate_model_location(model, self.root), model)
        for other in (self.folder / 'Other.app/tiny.pt', self.root / 'assets/models/large.pt'):
            with self.assertRaises(ValueError):
                runtime_paths.validate_model_location(other, self.root)
        external = self.folder / 'external.pt'
        external.write_bytes(b'external')
        self.assertEqual(runtime_paths.validate_model_location(external, self.root), external)
        model.unlink()
        model.symlink_to(self.folder / 'Other.app/tiny.pt')
        with self.assertRaises(ValueError):
            runtime_paths.validate_model_location(model, self.root)
        self.assertFalse(runtime_paths.is_packaged_model(model, self.root))

    def test_bundled_model_still_requires_official_hash_even_with_explicit_path(self):
        model = self.root / 'assets/models/tiny.pt'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'not the official tiny model')
        whisper = Mock()
        with patch.object(runtime_paths, 'ROOT', self.root), \
                patch.dict('sys.modules', {'torch': Mock(), 'whisper': whisper}):
            with self.assertRaisesRegex(ValueError, '官方 SHA256'):
                studio_transcribe._load_backend({'modelPath': str(model)}, None)
        whisper.load_model.assert_not_called()

    def test_only_own_unmodified_ffmpeg_is_accepted_inside_an_app(self):
        ffmpeg = self.root.parent / ('python/lib/python3.12/site-packages/imageio_ffmpeg/'
                                     'binaries/ffmpeg-macos-aarch64-v7.1')
        ffmpeg.parent.mkdir(parents=True)
        ffmpeg.write_bytes(b'our ffmpeg fixture')
        ffmpeg.chmod(0o755)
        self.marker['ffmpegSha256'] = hashlib.sha256(ffmpeg.read_bytes()).hexdigest()
        self.write_marker()
        with patch.object(runtime_paths, 'ROOT', self.root):
            self.assertEqual(_usable_tool(ffmpeg), str(ffmpeg))
            self.assertEqual(_ffmpeg_path(ffmpeg), ffmpeg)
            other = self.folder / 'Other.app/Contents/MacOS/ffmpeg'
            other.parent.mkdir(parents=True)
            other.write_bytes(ffmpeg.read_bytes())
            other.chmod(0o755)
            self.assertIsNone(_usable_tool(other))
            with self.assertRaisesRegex(ValueError, '其他应用原包'):
                _ffmpeg_path(other)
            ffmpeg.write_bytes(b'modified')
            self.assertIsNone(_usable_tool(ffmpeg))
            with self.assertRaises(ValueError):
                _ffmpeg_path(ffmpeg)

    def test_precompiled_ax_helper_is_verified_and_never_compiled_or_signed(self):
        helper = self.root / 'helpers/包房战神-rekordbox-AX.app/Contents/MacOS/rekordbox-ax'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b'our precompiled helper fixture')
        helper.chmod(0o755)
        self.marker['axHelperSha256'] = hashlib.sha256(helper.read_bytes()).hexdigest()
        self.write_marker()
        with patch.object(rekordbox_ax, 'ROOT', self.root), \
                patch.object(rekordbox_ax, 'RUNTIME', self.folder / 'user-helper-data'), \
                patch.object(rekordbox_ax, 'HELPER', helper), \
                patch.object(rekordbox_ax, 'inspect_original', return_value={'binary': 'original'}), \
                patch.object(rekordbox_ax.subprocess, 'run') as run:
            self.assertEqual(rekordbox_ax.prepare_helper(), helper)
            self.assertTrue((self.folder / 'user-helper-data').is_dir())
            helper.write_bytes(b'changed')
            with self.assertRaisesRegex(rekordbox_ax.ReaderError, '缺失或损坏'):
                rekordbox_ax.prepare_helper()
            run.assert_not_called()
        self.assertFalse((self.root / 'runtime').exists())


if __name__ == '__main__':
    unittest.main()
