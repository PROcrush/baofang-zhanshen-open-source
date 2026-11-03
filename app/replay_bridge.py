"""Explicit synthetic source, never controls a DJ application or plays audio."""
import json
import os
import select
import sys
import uuid

from app.clock import monotonic_ns


def serve():
    epoch = str(uuid.uuid4())
    decks = {key: {'versionId': f'demo:{key}', 'title': f'原创同步样例 {key}',
                   'path': '', 'positionMs': 0.0, 'durationMs': 120000.0, 'generation': 0}
             for key in ('A', 'B')}
    playing, speed, disconnected, loop = False, 1.0, False, False
    active = 'A'
    sequence = 0
    previous = monotonic_ns()
    input_buffer = b''
    while True:
        readable, _, _ = select.select([sys.stdin], [], [], 0.05)
        now = monotonic_ns()
        elapsed = (now - previous) / 1_000_000
        previous = now
        if playing:
            deck = decks[active]
            position = deck['positionMs'] + elapsed * speed
            if loop and position >= 16000:
                position = 8000 + (position - 8000) % 8000
                deck['generation'] += 1
            deck['positionMs'] = min(position, deck['durationMs'])
            if position >= deck['durationMs']:
                playing = False
        if readable:
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            input_buffer += chunk
        while b'\n' in input_buffer:
            line, input_buffer = input_buffer.split(b'\n', 1)
            try:
                command = json.loads(line)
                kind = command.get('type')
                if kind == 'shutdown':
                    return
                if kind == 'toggle':
                    playing = not playing
                elif kind == 'seek':
                    deck = decks[active]
                    deck['positionMs'] = max(0.0, min(float(command['seconds']) * 1000, deck['durationMs']))
                    deck['generation'] += 1
                elif kind == 'speed':
                    speed = max(.8, min(1.2, float(command['speed'])))
                elif kind == 'deck' and command['deck'] in decks:
                    active = command['deck']
                    decks[active]['positionMs'] = 0.0
                    decks[active]['generation'] += 1
                elif kind == 'load' and command['deck'] in decks:
                    active = command['deck']
                    track = command['track']
                    decks[active].update(versionId=track['versionId'], title=track['title'], path=track['path'],
                                         durationMs=track['durationMs'], positionMs=0.0)
                    decks[active]['generation'] += 1
                elif kind == 'disconnect':
                    disconnected = True
                elif kind == 'reconnect':
                    disconnected = False
                    epoch = str(uuid.uuid4())
                elif kind == 'loop':
                    loop = not loop
            except (ValueError, TypeError, KeyError):
                pass
        if disconnected:
            continue
        sequence += 1
        message = {'protocolVersion': 1, 'mode': 'replay', 'epoch': epoch, 'sequence': sequence,
                   'activeDeck': active, 'playing': playing, 'speed': speed, 'loopEnabled': loop, 'decks': {}}
        for key, source in decks.items():
            message['decks'][key] = dict(source, playing=playing and key == active,
                                         speed=speed, capturedNs=now, validUntilNs=now + 350_000_000)
        try:
            print(json.dumps(message, ensure_ascii=False), flush=True)
        except BrokenPipeError:
            break


if __name__ == '__main__':
    serve()
