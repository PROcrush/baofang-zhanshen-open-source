"""Show log: what reached the guest screen tonight, one JSON line per event.

Stored under runtime/show-log/YYYY-MM-DD.jsonl on this Mac only. No audio,
no keys; guest messages are kept as plain text the DJ approved.
"""
import csv
import json
import os
import time
from pathlib import Path

KINDS = {'song': '上屏歌曲', 'source': '换来源', 'hide': '隐藏歌词', 'restore': '恢复歌词',
         'tap': '打拍对齐', 'offset': '同步微调', 'message': '客屏插字', 'message_clear': '清除插字',
         'output': '客屏', 'midi': 'MIDI'}
MAX_DETAIL = 300
MAX_READ = 2000


class ShowLog:
    def __init__(self, folder, clock=time.time):
        self.folder = Path(folder)
        self.clock = clock

    def path_for(self, day=None):
        day = day or time.strftime('%Y-%m-%d', time.localtime(self.clock()))
        return self.folder / (day + '.jsonl')

    def add(self, kind, detail='', **extra):
        if kind not in KINDS:
            raise ValueError('未知记录类型')
        now = self.clock()
        row = {'t': round(now, 3), 'time': time.strftime('%H:%M:%S', time.localtime(now)),
               'kind': kind, 'detail': str(detail)[:MAX_DETAIL]}
        row.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool)) and k not in row})
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.path_for(), 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def days(self):
        if not self.folder.is_dir():
            return []
        return sorted((p.stem for p in self.folder.glob('*.jsonl')), reverse=True)

    def read(self, day=None, limit=MAX_READ):
        path = self.path_for(day)
        rows = []
        try:
            with open(path, encoding='utf-8') as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get('kind') in KINDS:
                        row['label'] = KINDS[row['kind']]
                        rows.append(row)
        except OSError:
            return []
        return rows[-limit:]

    def summary(self, rows):
        songs = [r for r in rows if r['kind'] == 'song']
        return {'songs': len(songs), 'hides': sum(r['kind'] == 'hide' for r in rows),
                'manual': sum(r['kind'] == 'source' for r in rows),
                'taps': sum(r['kind'] == 'tap' for r in rows),
                'messages': sum(r['kind'] == 'message' for r in rows)}

    def export_csv(self, day, destination):
        rows = self.read(day, limit=10 ** 6)
        destination = Path(destination)
        temporary = destination.with_suffix(destination.suffix + '.tmp')
        with open(temporary, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['时间', '类型', '内容'])
            for row in rows:
                writer.writerow([row.get('time', ''), row['label'], row.get('detail', '')])
        os.replace(temporary, destination)
        return len(rows)
