"""Offline library evidence using temporary audio only; any socket is forbidden."""
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.lyric_cache import atomic_bytes, checksum, encode_json, lyric_source_label
from app.storage import Library, inspect_audio, validate_timeline


def make_audio(path, amplitude=1200):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(struct.pack('<h', amplitude) * 32000)


def lyric(track):
    return {'schemaVersion': 1, 'versionId': track['versionId'], 'durationMs': 4000.,
            'quality': 'sentence', 'provider': 'offline-fixture',
            'segments': [{'startMs': 500., 'endMs': 1500., 'text': '断网歌词'},
                         {'startMs': 2500., 'endMs': 3500., 'text': '继续播放'}]}


class OfflineCacheTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch('socket.socket', side_effect=AssertionError('离线缓存不得访问网络'))
        self.network = self.network_guard.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.audio = self.root / 'audio' / '相同歌名.wav'
        make_audio(self.audio)
        self.track = inspect_audio(self.audio)
        self.library = Library(self.root / 'library')
        self.library.add_track(self.track)
        self.payload = lyric(self.track)

    def tearDown(self):
        self.library.close()
        self.temporary.cleanup()
        self.assertEqual(self.network.call_count, 0)
        self.network_guard.stop()

    def ready(self):
        self.library.add_timeline(self.payload, self.track)
        return self.library.cache_directory / (self.track['versionId'][7:] + '.json')

    def backup(self):
        self.ready()
        folder = self.root / 'backup'
        self.library.export_cache(folder)
        return folder

    def test_atomic_file_is_readable_after_restart_without_network(self):
        path = self.ready()
        self.assertEqual(json.loads(path.read_text()), self.payload)
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)
        self.assertTrue(self.library.offline_tracks()[0]['offlineReady'])
        self.assertEqual(self.library.cache_summary()['ready'], 1)

    def test_legacy_active_and_new_cloud_revision_remain_selectable_after_restart(self):
        version = self.track['versionId']
        imported = dict(self.payload, timingProvenance='imported_sentence')
        cloud = dict(self.payload, provider='bailian-asr', timingProvenance='provider_sentence')
        path = self.library.cache_directory / (version[7:] + '.json')
        atomic_bytes(path, encode_json(imported))
        imported_id = self.library.lyric_revisions(version)[0]['revisionId']
        cloud_id = self.library.add_timeline(cloud, self.track, activate=False)
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertEqual(self.library.timeline(version), imported)
        self.assertTrue(self.library.has_pending_lyric_revision(version))
        self.assertEqual(self.library.pending_lyric_revision(version), cloud)
        self.assertEqual([row['source'] for row in self.library.lyric_revisions(version)], ['导入字幕', '云端识别'])

        self.assertEqual(self.library.add_timeline(self.library.lyric_revision(version, cloud_id), self.track), cloud_id)
        self.assertFalse(self.library.has_pending_lyric_revision(version))
        self.assertEqual(self.library.lyric_revision(version, imported_id), imported)
        self.library.add_timeline(imported, self.track, activate=False)
        self.assertFalse(self.library.has_pending_lyric_revision(version))
        self.library.add_timeline(self.library.lyric_revision(version, imported_id), self.track)
        self.assertEqual(self.library.timeline(version), imported)

    def test_reference_correction_preserves_origin_on_repeated_edits(self):
        from worker.correction import correct_with_reference

        imported = copy.deepcopy(self.payload)
        imported['timingProvenance'] = 'imported_sentence'
        imported['segments'][0]['text'] = '深夜断网也能继续播放歌次'
        corrected = correct_with_reference(imported, '深夜断网也能继续播放歌词\n继续播放')
        self.assertEqual(corrected['timingProvenance'], 'corrected_provider_sentence')
        corrected = correct_with_reference(corrected, '深夜断网也能继续播放歌曲\n继续播放')
        self.library.add_timeline(corrected, self.track)
        self.assertEqual(lyric_source_label(self.library.timeline(self.track['versionId'])), '导入字幕')

    def test_interrupted_replacement_keeps_legacy_active_as_a_revision(self):
        version = self.track['versionId']
        path = self.library.cache_directory / (version[7:] + '.json')
        atomic_bytes(path, encode_json(self.payload))
        original_id = self.library.lyric_revisions(version)[0]['revisionId']
        updated = dict(self.payload, provider='bailian-asr')
        self.library.add_timeline(updated, self.track, activate=False)

        def replace_then_exit(target, data):
            atomic_bytes(target, data)
            raise SystemExit(1)

        with patch('app.storage.atomic_bytes', side_effect=replace_then_exit), self.assertRaises(SystemExit):
            self.library.add_timeline(updated, self.track)
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertEqual(self.library.timeline(version), updated)
        self.assertEqual(self.library.lyric_revision(version, original_id), self.payload)
        self.assertFalse(self.library.has_pending_lyric_revision(version))

    def test_cache_is_private_and_contains_only_whitelisted_lyrics(self):
        unsafe = dict(self.payload, apiKey='fixture-secret', taskId='fixture-task',
                      taskUrl='https://cloud.invalid/fixture-secret', audio='fixture-audio',
                      provider={'apiKey': 'nested-secret'})
        unsafe['segments'] = copy.deepcopy(unsafe['segments'])
        unsafe['segments'][0]['downloadUrl'] = 'https://cloud.invalid/download'
        self.library.add_timeline(unsafe, self.track)
        path = self.library.cache_directory / (self.track['versionId'][7:] + '.json')
        text = path.read_text()
        for value in ('fixture-secret', 'nested-secret', 'fixture-task', 'fixture-audio', 'cloud.invalid'):
            self.assertNotIn(value, text)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_lyric_is_exposed_and_not_loaded(self):
        track = self.library.offline_tracks()[0]
        self.assertFalse(track['offlineReady'])
        self.assertEqual(track['offlineState'], 'missing')
        self.assertEqual(self.library.cache_summary()['missing'], 1)

    def test_deleting_new_file_does_not_silently_restore_from_sqlite(self):
        path = self.ready()
        path.unlink()
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertEqual(self.library.offline_tracks()[0]['offlineState'], 'missing')

    def test_bad_file_rejected_even_when_sqlite_contains_good_copy(self):
        path = self.ready()
        path.write_text('{broken json')
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertEqual(self.library.offline_tracks()[0]['offlineState'], 'invalid')

    def test_structurally_wrong_cache_is_rejected(self):
        path = self.ready()
        invalid = dict(self.payload, durationMs=10)
        path.write_text(json.dumps(invalid))
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertEqual(self.library.cache_summary()['invalid'], 1)

    def test_other_version_in_correct_filename_is_rejected(self):
        path = self.ready()
        invalid = dict(self.payload, versionId='sha256:' + 'a' * 64)
        path.write_text(json.dumps(invalid))
        self.assertIsNone(self.library.timeline(self.track['versionId']))

    def test_in_memory_values_cannot_mutate_persistent_cache(self):
        self.ready()
        fetched = self.library.timeline(self.track['versionId'])
        fetched['segments'][0]['text'] = '错误改写'
        self.assertEqual(self.library.timeline(self.track['versionId'])['segments'][0]['text'], '断网歌词')

    def test_modified_audio_is_not_marked_offline_ready_without_full_hash(self):
        self.ready()
        details = self.audio.stat()
        make_audio(self.audio, 2400)
        os.utime(self.audio, ns=(details.st_atime_ns, details.st_mtime_ns))
        with patch('app.storage.inspect_audio', side_effect=AssertionError('列表不能重新哈希')):
            result = self.library.offline_tracks()[0]
        self.assertEqual(result['offlineState'], 'source_changed')
        self.assertFalse(result['offlineReady'])

    def test_missing_source_keeps_lyrics_but_is_not_offline_ready(self):
        self.ready()
        self.audio.unlink()
        self.assertEqual(self.library.offline_tracks()[0]['offlineState'], 'source_missing')
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)

    def test_source_reindex_of_same_content_restores_ready_after_rename(self):
        self.ready()
        moved = self.audio.with_name('重命名.wav')
        self.audio.rename(moved)
        self.library.add_track(inspect_audio(moved))
        self.assertEqual(self.library.cache_summary()['ready'], 1)
        self.assertEqual(self.library.offline_tracks()[0]['path'], str(moved.resolve()))

    def test_current_library_excludes_historical_replaced_version(self):
        self.ready()
        make_audio(self.audio, 2400)
        replacement = inspect_audio(self.audio)
        self.library.add_track(replacement)
        self.assertEqual(len(self.library.tracks()), 2)
        self.assertEqual(len(self.library.offline_tracks()), 1)
        self.assertEqual(self.library.offline_tracks()[0]['versionId'], replacement['versionId'])
        self.assertFalse(self.library.offline_tracks()[0]['offlineReady'])

    def test_review_is_visible_while_original_safe_lyrics_remain_playable(self):
        self.payload['correction'] = {'status': 'needs_review', 'review': [
            {'segmentIndex': 0, 'reasonCode': 'ambiguous_reference', 'originalText': '断网歌词'}],
            'changes': [], 'referenceSource': 'user_provided'}
        self.ready()
        track = self.library.offline_tracks()[0]
        self.assertTrue(track['offlineReady'])
        self.assertTrue(track['needsReview'])
        self.assertEqual(self.library.cache_summary()['review'], 1)

    def test_backup_contains_manifest_and_lyrics_only(self):
        backup = self.backup()
        self.library.record_job('cloud-fixture', 'ready', {'apiKey': 'not-in-backup'})
        report = self.library.export_cache(backup)
        self.assertEqual(report['exported'], 1)
        files = sorted(path.name for path in backup.iterdir())
        self.assertEqual(files, sorted(['manifest.json', self.track['versionId'][7:] + '.json']))
        self.assertNotIn(str(self.audio), (backup / 'manifest.json').read_text())
        self.assertNotIn('not-in-backup', ''.join(path.read_text() for path in backup.iterdir()))

    def test_restore_unknown_version_waits_for_sha_then_matches_renamed_copy(self):
        backup = self.backup()
        restored = Library(self.root / 'restored')
        try:
            report = restored.import_cache(backup)
            self.assertEqual((report['imported'], report['pending'], report['matched']), (1, 1, 0))
            self.assertEqual(restored.cache_summary()['total'], 0)
            moved = self.root / 'other' / '另一个文件名.wav'
            moved.parent.mkdir()
            shutil.copyfile(self.audio, moved)
            restored.add_track(inspect_audio(moved))
            self.assertEqual(restored.cache_summary()['ready'], 1)
            self.assertEqual(restored.timeline(self.track['versionId']), self.payload)
        finally:
            restored.close()

    def test_same_title_different_audio_does_not_bind_imported_cache(self):
        backup = self.backup()
        restored = Library(self.root / 'restored')
        try:
            restored.import_cache(backup)
            other = self.root / 'other' / self.audio.name
            make_audio(other, 2400)
            different = inspect_audio(other)
            restored.add_track(different)
            self.assertIsNone(restored.timeline(different['versionId']))
            self.assertFalse(restored.offline_tracks()[0]['offlineReady'])
        finally:
            restored.close()

    def test_restore_existing_version_keeps_newer_correction(self):
        backup = self.backup()
        updated = copy.deepcopy(self.payload)
        updated['segments'][0]['text'] = '已纠正歌词'
        self.library.add_timeline(updated, self.track)
        report = self.library.import_cache(backup)
        self.assertEqual((report['reused'], report['imported']), (1, 0))
        self.assertEqual(self.library.timeline(self.track['versionId']), updated)

    def test_import_changed_bytes_rejected_by_manifest_checksum(self):
        backup = self.backup()
        (backup / (self.track['versionId'][7:] + '.json')).write_text('{}')
        restored = Library(self.root / 'restored')
        try:
            report = restored.import_cache(backup)
            self.assertEqual((report['rejected'], report['imported']), (1, 0))
            self.assertIsNone(restored.timeline(self.track['versionId']))
        finally:
            restored.close()

    def test_import_traversal_and_duplicate_entries_are_rejected(self):
        backup = self.backup()
        manifest = json.loads((backup / 'manifest.json').read_text())
        good = dict(manifest['entries'][0])
        manifest['entries'] = [dict(good, file='../escape.json'), good, good]
        (backup / 'manifest.json').write_text(json.dumps(manifest))
        restored = Library(self.root / 'restored')
        try:
            report = restored.import_cache(backup)
            self.assertEqual((report['imported'], report['rejected']), (1, 2))
            self.assertFalse((self.root / 'escape.json').exists())
        finally:
            restored.close()

    def test_cache_and_backup_symlinks_are_refused(self):
        backup = self.backup()
        filename = self.track['versionId'][7:] + '.json'
        outside = self.root / 'outside.json'
        shutil.copyfile(backup / filename, outside)
        (backup / filename).unlink()
        (backup / filename).symlink_to(outside)
        restored = Library(self.root / 'restored')
        try:
            self.assertEqual(restored.import_cache(backup)['rejected'], 1)
            path = self.library.cache_directory / filename
            path.unlink()
            path.symlink_to(outside)
            self.assertIsNone(self.library.timeline(self.track['versionId']))
            self.assertEqual(self.library.offline_tracks()[0]['offlineState'], 'invalid')
        finally:
            restored.close()

    def test_symlink_manifest_and_directory_are_refused(self):
        backup = self.backup()
        original = backup / 'manifest.json'
        moved = self.root / 'manifest.json'
        original.rename(moved)
        original.symlink_to(moved)
        with self.assertRaises((OSError, ValueError)):
            self.library.import_cache(backup)
        link = self.root / 'backup-link'
        link.symlink_to(backup, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.library.import_cache(link)

    def test_legacy_sqlite_timeline_migrates_once(self):
        legacy_path = self.root / 'legacy'
        legacy = Library(legacy_path)
        legacy.add_track(self.track)
        legacy.connection.execute('DELETE FROM cache_metadata')
        legacy.connection.execute('INSERT INTO timelines VALUES (?,?)',
                                  (self.track['versionId'], json.dumps(self.payload)))
        legacy.connection.commit()
        legacy.close()
        legacy = Library(legacy_path)
        try:
            self.assertEqual(legacy.timeline(self.track['versionId']), self.payload)
            self.assertEqual(legacy.cache_summary()['ready'], 1)
        finally:
            legacy.close()

    def test_legacy_invalid_timeline_is_not_migrated(self):
        legacy_path = self.root / 'legacy'
        legacy = Library(legacy_path)
        legacy.add_track(self.track)
        legacy.connection.execute('DELETE FROM cache_metadata')
        legacy.connection.execute('INSERT INTO timelines VALUES (?,?)',
                                  (self.track['versionId'], '{broken'))
        legacy.connection.commit()
        legacy.close()
        legacy = Library(legacy_path)
        try:
            self.assertIsNone(legacy.timeline(self.track['versionId']))
            self.assertFalse(list(legacy.cache_directory.glob('*.json')))
        finally:
            legacy.close()

    def test_atomic_write_failure_preserves_existing_file_and_database(self):
        path = self.ready()
        previous = path.read_bytes()
        updated = copy.deepcopy(self.payload)
        updated['segments'][0]['text'] = '未写入改动'
        with patch('app.lyric_cache.os.replace', side_effect=OSError('fixture atomic failure')):
            with self.assertRaises(OSError):
                self.library.add_timeline(updated, self.track)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)
        self.assertFalse(list(path.parent.glob('.lyric-*')))

    def test_database_failure_rolls_back_new_file(self):
        path = self.ready()
        previous = path.read_bytes()
        self.library.connection.execute("CREATE TRIGGER refuse_lyric BEFORE INSERT ON timelines BEGIN SELECT RAISE(ABORT, 'fixture db failure'); END")
        self.library.connection.commit()
        updated = copy.deepcopy(self.payload)
        updated['segments'][0]['text'] = '未写入改动'
        with self.assertRaises(sqlite3.Error):
            self.library.add_timeline(updated, self.track)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)

    def test_cached_duration_fills_compressed_metadata_gap(self):
        self.ready()
        reindexed = dict(self.track, durationMs=None)
        self.library.add_track(reindexed)
        self.assertEqual(self.library.get_track(self.track['versionId'])['durationMs'], 4000.)

    def test_backup_can_restore_a_damaged_local_file(self):
        backup = self.backup()
        path = self.library.cache_directory / (self.track['versionId'][7:] + '.json')
        path.write_text('{broken')
        self.assertEqual(self.library.import_cache(backup)['imported'], 1)
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)

    def test_memo_checks_new_duration_without_rescanning_word_intervals(self):
        self.ready()
        with patch('app.storage.validate_timeline', wraps=validate_timeline) as validate:
            self.assertEqual(self.library.timeline(self.track['versionId']), self.payload)
            self.assertEqual(validate.call_count, 1)
            for invalid_duration in (5000., float('nan'), float('inf'), -1, True):
                self.assertIsNone(self.library.timeline(self.track['versionId'], invalid_duration))
            self.assertEqual(self.library.timeline(self.track['versionId'], 4000.), self.payload)
            self.assertEqual(validate.call_count, 1)

    def test_thousand_track_metadata_refresh_revalidates_only_changed_files(self):
        # Use real distinct WAV content and proofs. This checks operation counts
        # rather than machine-dependent timing or an artificial memo fixture.
        self.library.connection.execute('DELETE FROM tracks')
        self.library.connection.execute('DELETE FROM bindings')
        expected = []
        for index in range(1000):
            source = self.root / 'large-library' / str(index) / '同名歌曲.wav'
            make_audio(source, 1200 + index)
            track = inspect_audio(source)
            payload = lyric(track)
            payload['quality'] = 'word'
            for segment in payload['segments']:
                step = (segment['endMs'] - segment['startMs']) / len(segment['text'])
                segment['words'] = [{'text': text, 'startMs': segment['startMs'] + position * step,
                    'endMs': segment['startMs'] + (position + 1) * step}
                    for position, text in enumerate(segment['text'])]
            path = self.library.cache_directory / (track['versionId'][7:] + '.json')
            atomic_bytes(path, encode_json(payload))
            serialized = json.dumps(track, ensure_ascii=False)
            self.library.connection.execute('INSERT INTO tracks VALUES (?,?)', (track['versionId'], serialized))
            self.library.connection.execute('INSERT INTO bindings VALUES (?,?)', (track['path'], track['versionId']))
            expected.append((track, payload, path))
        self.library.connection.commit()

        with patch('app.storage.validate_timeline', wraps=validate_timeline) as validate, \
                patch('app.storage.copy.deepcopy', wraps=copy.deepcopy) as deepcopy, \
                patch('app.storage.inspect_audio', side_effect=AssertionError('列表刷新不能重新哈希')):
            rows = self.library.offline_tracks()
            self.assertEqual((len(rows), sum(row['offlineReady'] for row in rows)), (1000, 1000))
            self.assertEqual(validate.call_count, 1000)
            self.assertEqual(deepcopy.call_count, 0)

            # Repeated status/progress refreshes neither walk word intervals
            # nor copy full lyric trees; source and cache stats still execute.
            for _ in range(3):
                self.assertEqual(self.library.cache_summary()['ready'], 1000)
            self.assertEqual(validate.call_count, 1000)
            self.assertEqual(deepcopy.call_count, 0)

            track, payload, path = expected[0]
            fetched = self.library.timeline(track['versionId'])
            self.assertEqual(fetched, payload)
            self.assertEqual(deepcopy.call_count, 1)
            fetched['segments'][0]['words'][0]['text'] = '错'
            self.assertEqual(self.library.timeline(track['versionId']), payload)
            self.assertEqual(validate.call_count, 1000)

            damaged = dict(payload, durationMs=10)
            atomic_bytes(path, encode_json(damaged))
            summary = self.library.cache_summary()
            self.assertEqual((summary['ready'], summary['invalid']), (999, 1))
            self.assertEqual(validate.call_count, 1001)
            atomic_bytes(path, encode_json(payload))
            self.assertEqual(self.library.cache_summary()['ready'], 1000)
            self.assertEqual(validate.call_count, 1002)

            # File proof checks remain live even when every timeline is memoized.
            make_audio(Path(track['path']), 2400)
            changed = next(row for row in self.library.offline_tracks() if row['versionId'] == track['versionId'])
            self.assertEqual(changed['offlineState'], 'source_changed')
            self.assertFalse(changed['offlineReady'])
            self.assertEqual(validate.call_count, 1002)


if __name__ == '__main__':
    unittest.main()
