import copy
import unittest

from app.messages import accept_snapshot, validate_program_payload, validate_replay_snapshot


def deck(version='demo:A'):
    return {'versionId': version, 'title': '原创同步样例', 'path': '',
            'positionMs': 1000.0, 'durationMs': 120000.0, 'speed': 1.0,
            'capturedNs': 1_000_000_000, 'validUntilNs': 1_350_000_000,
            'playing': True, 'generation': 0}


def snapshot():
    return {'protocolVersion': 1, 'mode': 'replay', 'epoch': 'session-one', 'sequence': 2,
            'activeDeck': 'A', 'playing': True, 'speed': 1.0, 'loopEnabled': False,
            'decks': {'A': deck(), 'B': deck('demo:B')}}


def program():
    return {'state': {'selectedSource': 'auto', 'scene': 'amber', 'mode': 'replay'},
            'raw': snapshot()['decks'], 'timelines': {'demo:A': {
                'schemaVersion': 1, 'versionId': 'demo:A', 'durationMs': 120000,
                'quality': 'sentence', 'segments': [
                    {'startMs': 1000, 'endMs': 2000, 'text': '原创歌词'},
                ]}}}


class ReplayValidationTests(unittest.TestCase):
    def test_valid_snapshot_returned_without_mutation(self):
        message = snapshot()
        before = copy.deepcopy(message)
        self.assertIs(validate_replay_snapshot(message), message)
        self.assertEqual(message, before)

    def test_every_required_top_level_field_is_checked_transactionally(self):
        for field in snapshot():
            message = snapshot()
            del message[field]
            before = copy.deepcopy(message)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_replay_snapshot(message)
            self.assertEqual(message, before)

    def test_invalid_protocol_source_types_and_bounds_refused(self):
        changes = [('protocolVersion', True), ('protocolVersion', 1.0),
                   ('mode', 'live'), ('epoch', ''), ('epoch', 'x' * 129),
                   ('sequence', True), ('sequence', 0), ('sequence', 1.5),
                   ('activeDeck', 'C'), ('playing', 1), ('loopEnabled', 'false'),
                   ('speed', float('nan')), ('speed', float('inf')),
                   ('speed', .79), ('speed', 1.21), ('speed', 10 ** 1000)]
        for field, value in changes:
            message = snapshot()
            message[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_replay_snapshot(message)

    def test_exact_two_decks_and_required_fields(self):
        for decks in ({}, {'A': deck()}, {'A': deck(), 'B': deck(), 'C': deck()}, []):
            message = snapshot()
            message['decks'] = decks
            with self.subTest(decks=decks), self.assertRaises(ValueError):
                validate_replay_snapshot(message)
        for field in deck():
            message = snapshot()
            del message['decks']['B'][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_replay_snapshot(message)

    def test_deck_position_clock_types_and_ttl_reject_unsafe_estimates(self):
        changes = [('positionMs', -1), ('positionMs', 120001),
                   ('positionMs', float('nan')), ('positionMs', True),
                   ('durationMs', 0), ('durationMs', float('inf')),
                   ('speed', -.5), ('speed', float('nan')),
                   ('capturedNs', 0), ('capturedNs', 1.0), ('capturedNs', True),
                   ('capturedNs', 1 << 63), ('validUntilNs', 999_999_999),
                   ('validUntilNs', 2_000_000_001), ('validUntilNs', 1 << 63),
                   ('playing', None), ('generation', -1), ('generation', False),
                   ('versionId', ''), ('versionId', 'x' * 257),
                   ('title', None), ('title', 'x' * 513), ('path', 'x' * 8193)]
        for field, value in changes:
            message = snapshot()
            message['decks']['A'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_replay_snapshot(message)

    def test_valid_position_speed_and_ttl_endpoints(self):
        message = snapshot()
        message['speed'] = .8
        message['decks']['A'].update(positionMs=0, speed=.8,
                                     validUntilNs=1_000_000_000)
        message['decks']['B'].update(positionMs=120000, speed=1.2,
                                     validUntilNs=2_000_000_000)
        self.assertIs(validate_replay_snapshot(message), message)

    def test_duplicate_and_reordered_sequence_rejected_without_blocking_new_epoch(self):
        message = snapshot()
        self.assertTrue(accept_snapshot(None, 0, message))
        self.assertTrue(accept_snapshot('session-one', 1, message))
        self.assertFalse(accept_snapshot('session-one', 2, message))
        self.assertFalse(accept_snapshot('session-one', 3, message))
        message.update(epoch='session-two', sequence=1)
        self.assertTrue(accept_snapshot('session-one', 300, message))

    def test_new_epoch_does_not_bypass_payload_validation(self):
        message = snapshot()
        message['epoch'] = 'session-two'
        message['decks']['A']['positionMs'] = float('nan')
        with self.assertRaises(ValueError):
            accept_snapshot('session-one', 300, message)


class ProgramValidationTests(unittest.TestCase):
    def test_valid_program_and_empty_startup_raw_accepted(self):
        payload = program()
        self.assertIs(validate_program_payload(payload), payload)
        payload['raw'] = {}
        self.assertIs(validate_program_payload(payload), payload)

    def test_missing_core_fields_and_wrong_container_types_refused(self):
        for field in ('state', 'raw', 'timelines'):
            payload = program()
            del payload[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_program_payload(payload)
            payload = program()
            payload[field] = []
            with self.subTest(field=field, kind='list'), self.assertRaises(ValueError):
                validate_program_payload(payload)
        for field in ('selectedSource', 'scene'):
            payload = program()
            del payload['state'][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_program_payload(payload)

    def test_optional_core_state_fields_have_valid_shapes(self):
        changes = [('selectedSource', 'C'), ('scene', 'unknown'), ('mode', 'live'),
                   ('activeDeck', 'none'), ('effectiveSource', 'auto'),
                   ('playing', 'true'), ('stale', 1), ('speed', float('nan')),
                   ('lyric', []), ('deckA', None), ('diagnostics', 'bad'),
                   ('library', {}), ('notice', 1)]
        for field, value in changes:
            payload = program()
            payload['state'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_program_payload(payload)

    def test_timeline_version_and_invalid_time_refused(self):
        payload = program()
        payload['timelines']['demo:A']['versionId'] = 'demo:B'
        with self.assertRaises(ValueError):
            validate_program_payload(payload)
        payload = program()
        payload['timelines']['demo:A']['segments'][0]['endMs'] = float('nan')
        with self.assertRaises(ValueError):
            validate_program_payload(payload)
        payload = program()
        payload['timelines']['demo:A']['durationMs'] = 10 ** 1000
        with self.assertRaises(ValueError):
            validate_program_payload(payload)

    def test_partial_raw_is_never_a_valid_startup_payload(self):
        payload = program()
        del payload['raw']['B']
        with self.assertRaises(ValueError):
            validate_program_payload(payload)


if __name__ == '__main__':
    unittest.main()
