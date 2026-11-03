"""Append a test label; it never controls Serato or the subtitle application."""
import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from app.clock import monotonic_ns

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    if len(sys.argv) not in (2, 3):
        raise SystemExit('usage: python -m tools.mark_serato_phase PHASE [NOTE]')
    directory = root / 'runtime/test-20261009/live'
    directory.mkdir(parents=True, exist_ok=True)
    marker = {'phase': sys.argv[1], 'note': sys.argv[2] if len(sys.argv) == 3 else '',
              'capturedNs': monotonic_ns(),
              'time': datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    with (directory / 'phases.jsonl').open('a') as handle:
        handle.write(json.dumps(marker, ensure_ascii=False) + '\n')
    print(json.dumps(marker, ensure_ascii=False))
