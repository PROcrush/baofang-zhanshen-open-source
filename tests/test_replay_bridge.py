import json
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path


class ReplayBridgeTests(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([sys.executable, '-u', '-m', 'app.replay_bridge'],
                                      cwd=Path(__file__).resolve().parent.parent,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def tearDown(self):
        self.child.stdin.write('{"type":"shutdown"}\n')
        self.child.stdin.flush()
        try:
            self.child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait()
        self.child.stdin.close()
        self.child.stdout.close()

    def command(self, kind, **values):
        self.child.stdin.write(json.dumps(dict(values, type=kind)) + '\n')
        self.child.stdin.flush()

    def read_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if select.select([self.child.stdout], [], [], .25)[0]:
                sample = json.loads(self.child.stdout.readline())
                if condition(sample):
                    return sample
        self.fail('Replay bridge did not publish expected state')

    def test_pause_speed_seek_loop_and_epoch(self):
        initial = self.read_until(lambda x: not x['playing'])
        self.assertEqual(initial['decks']['A']['positionMs'], 0)
        self.command('toggle')
        playing = self.read_until(lambda x: x['playing'] and x['decks']['A']['positionMs'] > 10)
        self.command('speed', speed=1.2)
        self.read_until(lambda x: x['speed'] == 1.2)
        self.command('seek', seconds=15.95)
        self.command('loop')
        looped = self.read_until(lambda x: x['loopEnabled'] and 8000 <= x['decks']['A']['positionMs'] < 10000)
        self.assertGreater(looped['decks']['A']['generation'], playing['decks']['A']['generation'])
        self.command('toggle')
        paused = self.read_until(lambda x: not x['playing'])
        later = self.read_until(lambda x: x['sequence'] >= paused['sequence'] + 3)
        self.assertEqual(paused['decks']['A']['positionMs'], later['decks']['A']['positionMs'])
        self.command('reconnect')
        reconnected = self.read_until(lambda x: x['epoch'] != initial['epoch'])
        self.assertEqual(reconnected['mode'], 'replay')

    def test_loading_file_changes_version_and_never_plays_sound(self):
        self.read_until(lambda x: True)
        self.command('load', deck='B', track={'versionId': 'sha256:test-version', 'title': 'test', 'path': '/tmp/test.wav', 'durationMs': 25000})
        result = self.read_until(lambda x: x['decks']['B']['versionId'] == 'sha256:test-version')
        self.assertEqual(result['activeDeck'], 'B')
        self.assertEqual(result['decks']['B']['durationMs'], 25000)
        self.assertFalse(result['playing'])


if __name__ == '__main__':
    unittest.main()
