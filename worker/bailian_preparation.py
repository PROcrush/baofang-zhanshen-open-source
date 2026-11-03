"""Library adapter for the same Bailian plan, receipt and result as single songs."""
import argparse
import json
import os
from pathlib import Path

from worker.ai_tasks import run as ai_run
from worker.bailian import run_job
from worker.cloud_pipeline import _atomic


def run(action, request, receipt, key='', *, transport=None, poll_limit=20):
    receipt = Path(receipt)
    if action == 'preflight':
        return ai_run(dict(request, action='plan', scope='library', snapshot=''), transport=transport)
    source = receipt.with_name('source.json')
    if action == 'start':
        plan = request
        # A single-song source contains its review snapshot; preserve it.
        if not source.exists():
            _atomic(source, {'config': plan['config'], 'plan': plan})
    else:
        saved = json.loads(source.read_text())
        if saved['config'] != request['config']:
            raise ValueError('原任务的百炼地址或模型已变化，请恢复原配置后续查')
        plan = dict(saved['plan'], sourcePath=request['sourcePath'])
    result = run_job(plan, receipt, action='recognize' if action == 'start' else 'query',
                     key=key, transport=transport, poll_limit=poll_limit)
    if result.get('proposal', {}).get('timeline'):
        path = receipt.with_name('timeline.json')
        _atomic(path, result['proposal']['timeline'])
        result = dict(result, timelinePath=str(path))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=('preflight', 'start', 'query'), required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args()
    key = os.environ.pop('BAOFANG_TASK_KEY', '')
    try:
        result = run(args.action, json.loads(Path(args.plan).read_text()), args.receipt, key)
    except Exception as exc:
        message = str(exc).replace(key, '[密钥]') if key else str(exc)
        result = {'phase': 'failed', 'detail': message[:1000]}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 2 if result.get('phase') == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
