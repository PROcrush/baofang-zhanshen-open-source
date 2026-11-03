"""Native Keychain smoke test with a unique fake-only item and cleanup."""
import json
import subprocess
import sys
import uuid

from app.credentials import KeychainStore


def main():
    if len(sys.argv) > 1:
        # Secrets, including this test value, enter through stdin, not argv.
        service, action = sys.argv[1:]
        store = KeychainStore(service)
        expected = sys.stdin.read()
        if action == 'write':
            store.write('fixture-host', expected)
        elif action == 'read':
            assert store.read('fixture-host') == expected
        return
    service = 'local.baofangzhanshen.keychain-test.' + str(uuid.uuid4())
    store = KeychainStore(service)
    try:
        for action, value in [('write', 'FAKE_KEY_ONE'), ('read', 'FAKE_KEY_ONE'),
                              ('write', 'FAKE_KEY_TWO'), ('read', 'FAKE_KEY_TWO')]:
            subprocess.run([sys.executable, '-m', 'tools.check_keychain', service, action],
                           input=value, text=True, check=True, timeout=15, capture_output=True)
    finally:
        store.delete('fixture-host')
    assert not store.read('fixture-host')
    print(json.dumps({'passed': True, 'separateProcessRecovery': True,
                      'updated': True, 'deleted': True, 'realKeysRead': False}))


if __name__ == '__main__':
    main()
