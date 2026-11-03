"""Opt-in real-song acceptance probe. Default: no network, no new task.

Reads only the application's remembered Keychain item. Logs request kinds and
task IDs, never keys, headers, signed URLs, upload policies or audio bodies.
"""
import argparse
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.credentials import KeychainStore
from app.storage import inspect_audio
from worker.ai_providers import HTTPTransport, profile
from worker.ai_tasks import run as single_run
from worker.bailian import receipt_path
from worker.bailian_preparation import run as batch_run
from worker.cloud_pipeline import _atomic


class AuditTransport:
    def __init__(self, path, *, execute=False, allow_new=False):
        self.path, self.execute, self.allow_new = Path(path), execute, allow_new
        self.calls = []
        self.transport = HTTPTransport()

    def record(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, 'a') as stream:
            stream.write(json.dumps(dict(at=time.time(), **event)) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def request(self, url, headers, body=None, **kwargs):
        parsed = urlsplit(url)
        kind = ('submit' if parsed.path.endswith('/transcription') else
                'query' if '/api/v1/tasks/' in parsed.path else
                'policy' if parsed.path == '/api/v1/uploads' else
                'upload' if (parsed.hostname or '').startswith('dashscope-file-') else 'download')
        if not self.execute or (kind in ('policy', 'upload', 'submit') and not self.allow_new):
            raise ValueError('本次仅验证原任务复用，已阻止新增网络或识别提交')
        event = {'kind': kind, 'host': parsed.hostname, 'method': kwargs.get('method', 'POST')}
        self.calls.append(event)
        self.record(dict(event, phase='sent'))
        try:
            payload, response_headers = self.transport.request(url, headers, body, **kwargs)
        except Exception as exc:
            self.record(dict(event, phase='error', errorType=type(exc).__name__))
            raise
        output = payload.get('output', {}) if isinstance(payload, dict) else {}
        safe = {name: output[name] for name in ('task_id', 'task_status') if name in output}
        if isinstance(payload, dict):
            safe['requestId'] = payload.get('request_id')
            usage = payload.get('usage') or {}
            safe['usage'] = {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                             if k == 'duration' and isinstance(v, (int, float))}
        self.record(dict(event, phase='received', **safe))
        return payload, response_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', nargs='+')
    parser.add_argument('--runtime', type=Path, default=Path('runtime'))
    parser.add_argument('--evidence', type=Path, default=Path('runtime/bailian-live-20261023'))
    parser.add_argument('--entry', choices=('single', 'batch'), default='single')
    parser.add_argument('--execute', action='store_true', help='Allow real queries; still no new submissions without an explicit budget')
    parser.add_argument('--max-new-cost-cny', type=float, default=0)
    args = parser.parse_args()
    if not math.isfinite(args.max_new_cost_cny) or not 0 <= args.max_new_cost_cny <= 1:
        raise ValueError('实测新增预算必须为 0–1 元的有限数值')
    metadata = json.loads((args.runtime / 'ai-profiles.json').read_text())
    clean = profile('bailian-asr')
    saved = metadata.get('profiles', {}).get('bailian-asr', clean)
    clean = profile('bailian-asr', saved['baseUrl'], saved['model'])
    planned = []
    for path in args.audio:
        track = inspect_audio(path)
        request = dict(action='plan', config=clean, track=track, scope='studio',
                       snapshot='', planId='live-'+str(time.time_ns()))
        plan = single_run(request)['plan']
        receipt = receipt_path(args.runtime.resolve(), clean, plan['versionId'])
        planned.append((track, plan, receipt))
    new_plans = [plan for _, plan, receipt in planned if not receipt.exists()]
    cost = (sum(plan['estimatedCostCNY'] for plan in new_plans)
            if all(plan['estimatedCostCNY'] is not None for plan in new_plans) else None)
    if new_plans and (not args.execute or args.max_new_cost_cny <= 0
                      or cost is None or cost > args.max_new_cost_cny):
        print(json.dumps({'phase': 'preflight-only', 'newEstimatedCNY': cost,
                          'tracks': [{'title': t['title'], 'versionId': p['versionId'],
                                      'durationMs': p['durationMs'], 'hasReceipt': r.exists()}
                                     for t, p, r in planned]}, ensure_ascii=False))
        return
    host = 'https://' + urlsplit(clean['baseUrl']).netloc
    key = ''
    if args.execute:
        if host not in metadata.get('rememberedBailianHosts', []):
            raise ValueError('请先在应用中安全记住当前百炼 Key')
        key = KeychainStore().read(host)
        if not key:
            raise ValueError('系统钥匙串未恢复 Key')
    reports = []
    for track, plan, receipt in planned:
        existing = receipt.exists()
        audit = AuditTransport(args.evidence / 'requests.jsonl', execute=args.execute, allow_new=not existing)
        request = dict(action='query' if existing else 'recognize', config=clean, track=track,
                       scope='studio', snapshot='', rows=[], timeline=None,
                       plan=plan, receipt=str(receipt))
        if not receipt.with_name('source.json').exists():
            # The probe has no editor snapshot. Let the UI capture the current
            # lyrics when it first opens this job for review, as batch does.
            _atomic(receipt.with_name('source.json'), {'config': clean, 'plan': plan})
        before = receipt.read_bytes() if existing else None
        if args.entry == 'single':
            result = single_run(request, key, transport=audit, poll_limit=1)
        else:
            result = batch_run('query' if existing else 'start',
                               dict(config=clean, sourcePath=track['path']) if existing else plan,
                               receipt, key, transport=audit, poll_limit=1)
        timeline = result.get('proposal', {}).get('timeline', {})
        reports.append({'title': track['title'], 'versionId': plan['versionId'], 'entry': args.entry,
                        'phase': result['phase'], 'taskId': result.get('taskId'),
                        'keyRestored': bool(key), 'hadReceipt': existing,
                        'receiptUnchanged': before == receipt.read_bytes() if existing else False,
                        'segments': len(timeline.get('segments', [])),
                        'words': sum(len(row.get('words', [])) for row in timeline.get('segments', [])),
                        'requests': {kind: sum(call['kind'] == kind for call in audit.calls)
                                     for kind in ('policy', 'upload', 'submit', 'query', 'download')}})
    payload = {'at': time.time(), 'newEstimatedCNY': cost, 'results': reports,
               'billingStatementVerified': False}
    path = args.evidence / (args.entry+'-'+str(time.time_ns())+'.json')
    _atomic(path, payload)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
