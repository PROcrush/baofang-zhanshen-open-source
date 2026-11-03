import copy
import unittest

from app.recognition_policy import decide_recognition


VERSION = 'sha256:' + 'a' * 64
OTHER_VERSION = 'sha256:' + 'b' * 64


def timeline(version=VERSION):
    return {'schemaVersion': 1, 'versionId': version, 'durationMs': 4000,
            'quality': 'sentence', 'segments': [
                {'startMs': 1000, 'endMs': 2000, 'text': '原创歌词'}]}


def job(state='running', version=VERSION):
    return {'versionId': version, 'state': state, 'jobId': 'provider-job', 'intentId': 'old-intent'}


class RecognitionPolicyTests(unittest.TestCase):
    def test_actual_sha_identity_required_and_auto_paid_defaults_off(self):
        for version in (None, 'same song title', 'demo:A', 'sha256:abc', 'sha256:' + 'A' * 64):
            with self.subTest(version=version):
                result = decide_recognition(version, automatic_paid_enabled=True, intent_id='new-intent')
                self.assertEqual(result['action'], 'blocked')
                self.assertEqual(result['reasonCode'], 'identity_unverified')
                self.assertFalse(result['networkRequest'])
        result = decide_recognition(VERSION)
        self.assertEqual(result['reasonCode'], 'automatic_paid_disabled')
        self.assertIsNone(result['newIntent'])

    def test_exact_version_cache_serves_even_when_auto_paid_disabled(self):
        cached = timeline()
        result = decide_recognition(VERSION, cached_timeline=cached)
        self.assertEqual(result['action'], 'use_cache')
        self.assertTrue(result['cachedTimelineReady'])
        self.assertIs(result['retainedTimeline'], cached)
        self.assertFalse(result['networkRequest'])

    def test_wrong_version_cache_and_invalid_timeline_never_trigger_auto_submission(self):
        cases = [timeline(OTHER_VERSION), timeline()]
        cases[1]['segments'][0]['endMs'] = float('nan')
        for cached in cases:
            with self.subTest(cached=cached):
                result = decide_recognition(VERSION, cached_timeline=cached,
                                            automatic_paid_enabled=True, intent_id='new-intent')
                self.assertEqual(result['reasonCode'], 'cache_invalid')
                self.assertIsNone(result['newIntent'])

    def test_two_decks_reuse_one_running_version_task(self):
        existing = job()
        first = decide_recognition(VERSION, job=existing, automatic_paid_enabled=True)
        second = decide_recognition(VERSION, job=existing, automatic_paid_enabled=True)
        self.assertEqual(first, second)
        self.assertEqual(first['action'], 'reuse_running')
        self.assertEqual(first['jobId'], 'provider-job')
        self.assertEqual(first['intentId'], 'old-intent')
        self.assertIsNone(first['newIntent'])

    def test_queued_uploading_and_persisted_intent_do_not_resubmit(self):
        for state in ('queued', 'submitted', 'polling', 'uploading', 'intent_persisted'):
            result = decide_recognition(VERSION, job=job(state), automatic_paid_enabled=True,
                                        intent_id='new-intent')
            with self.subTest(state=state):
                self.assertEqual(result['action'], 'reuse_running')
                self.assertIsNone(result['newIntent'])

    def test_unknown_and_unrecognized_provider_states_must_reconcile_first(self):
        for state in ('unknown', 'submission_unknown', 'provider-new-state', None, {}):
            result = decide_recognition(VERSION, job=job(state), automatic_paid_enabled=True,
                                        intent_id='new-intent')
            with self.subTest(state=state):
                self.assertEqual(result['action'], 'reconcile_unknown')
                self.assertTrue(result['unresolvedJob'])
                self.assertIsNone(result['newIntent'])

    def test_completed_task_without_cache_recovers_result_and_failed_requires_explicit_retry(self):
        for state in ('ready', 'completed', 'succeeded'):
            result = decide_recognition(VERSION, job=job(state), automatic_paid_enabled=True)
            self.assertEqual(result['action'], 'recover_result')
            self.assertIsNone(result['newIntent'])
        result = decide_recognition(VERSION, job=job('failed'), automatic_paid_enabled=True)
        self.assertEqual(result['reasonCode'], 'previous_failed')

    def test_force_uses_new_intent_and_retains_old_good_timeline_and_task(self):
        cached, existing = timeline(), job()
        before = copy.deepcopy((cached, existing))
        result = decide_recognition(VERSION, cached_timeline=cached, job=existing,
                                    force=True, intent_id='forced-intent')
        self.assertEqual(result['action'], 'new_intent')
        self.assertEqual(result['newIntent']['intentId'], 'forced-intent')
        self.assertTrue(result['newIntent']['forced'])
        self.assertTrue(result['preserveTimeline'])
        self.assertIs(result['retainedTimeline'], cached)
        self.assertEqual(result['jobId'], 'provider-job')
        self.assertFalse(result['networkRequest'])
        self.assertEqual((cached, existing), before)

    def test_force_does_not_silently_reuse_old_intent_identifier(self):
        result = decide_recognition(VERSION, job=job(), force=True, intent_id='old-intent')
        self.assertEqual(result['reasonCode'], 'force_intent_conflict')
        self.assertIsNone(result['newIntent'])
        result = decide_recognition(VERSION, force=True)
        self.assertEqual(result['action'], 'new_intent_required')

    def test_auto_enabled_creates_intent_only_after_caller_provides_identifier(self):
        result = decide_recognition(VERSION, automatic_paid_enabled=True)
        self.assertEqual(result['action'], 'new_intent_required')
        result = decide_recognition(VERSION, automatic_paid_enabled=True, intent_id='new-intent')
        self.assertEqual(result['action'], 'new_intent')
        self.assertEqual(result['newIntent']['versionId'], VERSION)
        self.assertFalse(result['newIntent']['forced'])
        self.assertFalse(result['networkRequest'])

    def test_new_file_version_does_not_reuse_another_versions_running_job(self):
        result = decide_recognition(OTHER_VERSION, job=job(), automatic_paid_enabled=True,
                                    intent_id='another-file-intent')
        self.assertEqual(result['action'], 'new_intent')
        self.assertIsNone(result['jobId'])
        self.assertEqual(result['newIntent']['versionId'], OTHER_VERSION)

    def test_cached_timeline_remains_usable_during_unknown_remote_status(self):
        result = decide_recognition(VERSION, cached_timeline=timeline(), job=job('unknown'))
        self.assertEqual(result['action'], 'use_cache')
        self.assertTrue(result['unresolvedJob'])
        self.assertIsNone(result['newIntent'])

    def test_non_boolean_policy_values_do_not_authorize_a_new_intent(self):
        result = decide_recognition(VERSION, automatic_paid_enabled='true', intent_id='new-intent')
        self.assertEqual(result['reasonCode'], 'policy_invalid')
        self.assertIsNone(result['newIntent'])

    def test_malformed_persisted_job_record_cannot_turn_into_a_new_paid_intent(self):
        for existing in ('unreadable record', {}, {'state': 'running'}):
            with self.subTest(existing=existing):
                result = decide_recognition(VERSION, job=existing,
                                            automatic_paid_enabled=True, intent_id='new-intent')
                self.assertEqual(result['action'], 'reconcile_unknown')
                self.assertEqual(result['reasonCode'], 'job_record_invalid')
                self.assertIsNone(result['newIntent'])


if __name__ == '__main__':
    unittest.main()
