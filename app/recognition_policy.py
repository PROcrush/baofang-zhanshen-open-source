"""Local recognition intent policy; no network, payment, or persistence.

The caller persists a returned new intent before any external submission and
supplies its unique ID. A provider adapter still needs its own preflight and
permission checks. This policy alone cannot guarantee exactly-once billing.
"""
import re

from app.storage import validate_timeline


SHA_VERSION = re.compile(r'sha256:[0-9a-f]{64}\Z')
ACTIVE_STATES = frozenset({'queued', 'running', 'submitted', 'polling', 'uploading', 'intent_persisted'})
COMPLETE_STATES = frozenset({'ready', 'completed', 'succeeded'})
FAILED_STATES = frozenset({'failed', 'cancelled'})


def _identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def decide_recognition(version_id, *, cached_timeline=None, job=None,
                       automatic_paid_enabled=False, force=False, intent_id=None):
    """Return an immutable-input decision for one exact SHA256 file version.

    Jobs use versionId/state/jobId/intentId. Unknown or unrecognized states
    never automatically resubmit. Explicit force requires a fresh caller ID
    and retains the old timeline. With no supplied ID, a permitted new request
    reports new_intent_required so the caller can first allocate/persist it.
    """
    matching_job = job if isinstance(job, dict) and job.get('versionId') == version_id else None
    malformed_job = job is not None and (not isinstance(job, dict)
                    or not isinstance(job.get('versionId'), str)
                    or SHA_VERSION.fullmatch(job['versionId']) is None)
    state = matching_job.get('state') if matching_job else None
    active = isinstance(state, str) and state in ACTIVE_STATES
    complete = isinstance(state, str) and state in COMPLETE_STATES
    failed = isinstance(state, str) and state in FAILED_STATES
    unresolved = malformed_job or (matching_job is not None and not active and not complete and not failed)
    existing_intent = matching_job.get('intentId') if matching_job else None
    existing_job = matching_job.get('jobId') if matching_job else None
    cached_ready = False
    if isinstance(version_id, str) and SHA_VERSION.fullmatch(version_id) and cached_timeline is not None:
        try:
            validate_timeline(cached_timeline, version_id)
            cached_ready = True
        except (ValueError, TypeError, KeyError, OverflowError):
            pass

    def result(action, code, reason, new_intent=None):
        return {'action': action, 'reasonCode': code, 'reason': reason,
                'versionId': version_id if isinstance(version_id, str) else None,
                'jobId': existing_job if _identifier(existing_job) else None,
                'intentId': new_intent['intentId'] if new_intent else
                            (existing_intent if _identifier(existing_intent) else None),
                'newIntent': new_intent, 'cachedTimelineReady': cached_ready,
                'preserveTimeline': cached_timeline is not None,
                'retainedTimeline': cached_timeline if cached_ready else None,
                'unresolvedJob': unresolved, 'networkRequest': False}

    if not isinstance(version_id, str) or SHA_VERSION.fullmatch(version_id) is None:
        return result('blocked', 'identity_unverified', '先核验实际文件 SHA256，再决定识别')
    if not isinstance(force, bool) or not isinstance(automatic_paid_enabled, bool):
        return result('blocked', 'policy_invalid', '识别策略开关无效')
    if force:
        if not _identifier(intent_id):
            return result('new_intent_required', 'force_needs_new_intent', '强制重识别需要先分配新的提交意向；旧歌词保留')
        if intent_id == existing_intent:
            return result('blocked', 'force_intent_conflict', '强制重识别必须使用新的意向编号，不能覆盖原任务')
        new_intent = {'intentId': intent_id, 'versionId': version_id,
                      'state': 'intent_persisted', 'forced': True}
        return result('new_intent', 'forced_new_intent', '已规划新的强制识别意向；旧歌词继续保留，尚未提交', new_intent)
    if cached_ready:
        return result('use_cache', 'cache_hit', '使用该实际文件版本的已校验歌词缓存')
    if active:
        return result('reuse_running', 'existing_running', '复用同版本进行中的任务，等待或续查，不重复提交')
    if unresolved:
        return result('reconcile_unknown', 'job_record_invalid' if malformed_job else 'submission_unknown',
                      '已有任务提交状态未知，先核对回执，不重复提交')
    if complete:
        return result('recover_result', 'result_missing', '同版本任务已完成，先恢复结果，不重新提交')
    if failed:
        return result('blocked', 'previous_failed', '保留失败任务记录；显式重试才创建新识别意向')
    if automatic_paid_enabled is not True:
        return result('blocked', 'automatic_paid_disabled', '自动付费识别默认关闭；当前保持纯视觉')
    if cached_timeline is not None:
        return result('blocked', 'cache_invalid', '缓存未通过版本或时间轴校验，先核对结果，不自动新增提交')
    if not _identifier(intent_id):
        return result('new_intent_required', 'new_intent_needed', '自动识别策略已启用；先分配并保存提交意向')
    new_intent = {'intentId': intent_id, 'versionId': version_id,
                  'state': 'intent_persisted', 'forced': False}
    return result('new_intent', 'automatic_new_intent', '已规划新识别意向；尚未上传或提交', new_intent)
