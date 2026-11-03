"""One isolated indexing job; returns one bounded JSON response."""
import json
import sys
from app.storage import inspect_audio

if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('需要一个音频路径')
        result = {'ok': True, 'track': inspect_audio(sys.argv[1])}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(0 if result['ok'] else 1)
