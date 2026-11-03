"""Isolated AI tasks; credentials enter only via the task-scoped environment."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

from worker.ai_providers import correct_lines, profile, test_text, transcribe
from worker.cloud_pipeline import _atomic, _read_json, _verify_source, preflight, run_job


def _reference_source(track):
    path=Path(track['path']).resolve(strict=True)
    before=path.stat()
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    after=path.stat()
    fields=('st_size','st_mtime_ns','st_ctime_ns','st_ino','st_dev')
    if any(getattr(before,field)!=getattr(after,field) for field in fields) or 'sha256:'+digest.hexdigest()!=track['versionId']:
        raise ValueError('实际音频版本已经改变，请重新导入歌曲')
    return tuple(getattr(after,field) for field in fields)


def _reference_task(request,transport):
    from worker.lyric_reference import search, validate_reference
    from worker.reference_match import match, rank
    before=_reference_source(request['track'])
    if not request['rows']:
        raise ValueError('先导入或识别带实际时间码的歌词')
    if request['action']=='search-reference':
        result=search(request['title'],request.get('artist',''),request['cacheDirectory'],
                      offline=request.get('offline',False),transport=transport)
        cards,proposal=rank(request['rows'],result['candidates'],request['title'],request.get('artist',''))
        result.update(candidates=cards,phase='review' if proposal else 'choose-reference')
        if proposal:
            result['proposal']=proposal
        result['detail']=('已复用本地原版歌词' if result['cached'] else '已找到原版歌词')+(
            f"，生成{len(proposal['changes'])}句建议；请审阅后应用" if proposal else '；请选择歌手和来源后生成建议')
    else:
        value=validate_reference(request['reference'])
        proposal=match(request['rows'],value)
        result={'phase':'review','proposal':proposal,
                'detail':f"已对照{value['artist']}的原版歌词，生成{len(proposal['changes'])}句建议；实际时间码保持原样"}
    if before!=_reference_source(request['track']):
        raise ValueError('匹配期间音频改变，请重新导入歌曲')
    return result


def run(request, key='', *, transport=None, poll_limit=1):
    action=request['action']
    if action in ('search-reference','match-reference'):
        return _reference_task(request,transport)
    config=request['config']
    clean=profile(config['provider'],config['baseUrl'],config['model'],budget=config.get('budgetPTC',0))
    if action=='plan':
        host=clean['baseUrl'] if clean['provider']=='302-singing' else 'api.302ai.cn'
        plan=preflight(request['track'],host,clean.get('budgetPTC',0))
        plan.update(config=clean,scope=request['scope'],snapshot=request['snapshot'],planId=request['planId'])
        if clean['provider']!='302-singing':
            from worker.ai_providers import MAX_AUDIO_BYTES
            if clean['provider']=='bailian-asr':
                from worker.bailian import AUDIO_SUFFIXES, MAX_AUDIO_BYTES as bailian_limit, config as bailian_config
                bailian_config(clean)
                if plan['size']>bailian_limit or Path(plan['sourcePath']).suffix.lower() not in AUDIO_SUFFIXES:
                    raise ValueError('百炼识别支持50MB以内的常见音频或MP4文件')
                plan['estimatedCostCNY']=round(plan['durationMs']/1000*0.00022,6) if clean['model']=='fun-asr' else None
                plan['costEstimateBasisCNY']='Fun-ASR北京公开价0.00022元/秒，按整段时长保守估算；优惠和实际账单由百炼结算'
            elif plan['size']>MAX_AUDIO_BYTES or Path(plan['sourcePath']).suffix.lower() not in ('.wav','.mp3','.ogg'):
                raise ValueError('直接上传支持25MB以内的WAV、MP3或OGG；其他格式请先转换')
            plan['provider']=clean['provider']
            for field in ('estimatedCostPTC','maxCostPTC','budgetAllowed','costEstimateBasis'):
                plan.pop(field,None)
        if plan['durationMs']>120*60000:
            raise ValueError('单次识别限两小时以内，请先拆分长音频')
        return {'phase':'planned','plan':plan,'detail':'音频已在本机核验，尚未上传'}
    if action=='test':
        if clean['kind']=='speech':
            return {'phase':'configured','detail':'地址、模型与Key格式已核验，尚未上传音频或验证识别服务'}
        result=test_text(clean,key,transport)
        return {'phase':'connected','detail':result['detail']}
    if action=='test-bailian':
        from worker.bailian import config as bailian_config, upload_policy
        from worker.ai_providers import HTTPTransport
        transport=transport or HTTPTransport()
        speech=bailian_config(request['speechConfig'])
        results={}
        for kind,operation in (('speech',lambda:upload_policy(speech,key,transport)),
                               ('text',lambda:test_text(clean,key,transport))):
            try:
                operation()
                results[kind]='上传权限已核验，识别待歌曲实测' if kind=='speech' else '文字模型连接成功'
            except ValueError as exc:
                results[kind]=str(exc)
        return {'phase':'checked','capabilities':results,'detail':'百炼连接检查完成；未上传歌曲，识别能力需实际音频验证'}
    if action=='correct':
        proposal=correct_lines(clean,key,request['rows'],transport)
        return {'phase':'review','proposal':proposal,'detail':f"已生成{len(proposal['changes'])}句纠错建议，审阅后应用；时码保持原样"}
    if action not in ('recognize','query'):
        raise ValueError('AI任务类型无效')
    plan=request['plan']
    if plan.get('config')!=clean:
        raise ValueError('模型配置与识别计划不一致，请重新预检')
    receipt=Path(request['receipt'])
    if clean['provider']=='bailian-asr':
        from worker.bailian import run_job as bailian_job
        return bailian_job(plan,receipt,action=action,key=key,transport=transport,poll_limit=poll_limit)
    if clean['provider']=='302-singing':
        result=run_job(plan,receipt,action='query' if action=='query' else 'start',key=key,transport=transport,poll_limit=1)
        if result.get('timelinePath'):
            result['proposal']={'timeline':_read_json(result['timelinePath'])}
        return result
    if action=='query':
        raise ValueError('该同步接口没有续查任务，不自动重新提交')
    descriptor=os.open(receipt.with_name(receipt.name+'.lock'),os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _recognize_once(clean,key,plan,receipt,transport)
    except BlockingIOError as exc:
        raise ValueError('该识别计划正在处理，不重复上传') from exc
    finally:
        os.close(descriptor)


def _recognize_once(clean,key,plan,receipt,transport):
    # Durable intent comes before networking. Re-running this exact plan is
    # never a reason to upload again, including after timeout or cancellation.
    if receipt.exists():
        raise ValueError('该识别计划已有提交记录，不重复上传；请核对结果或手动创建新计划')
    _verify_source(dict(plan,maxCostPTC=plan.get('maxCostPTC',0)))
    _atomic(receipt,{'planId':plan['planId'],'versionId':plan['versionId'],'config':clean,'state':'unknown','networkStarted':True})
    proposal=transcribe(clean,key,plan['sourcePath'],plan['versionId'],plan['durationMs'],transport)
    _verify_source(dict(plan,maxCostPTC=plan.get('maxCostPTC',0)))
    _atomic(receipt,{'planId':plan['planId'],'versionId':plan['versionId'],'config':clean,'state':'ready'})
    return {'phase':'review','proposal':proposal,'detail':'识别已返回，审阅后应用' if proposal.get('timeline') else '识别已返回文字，没有时间码；可保存为参考歌词'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:
        request=_read_json(args.request)
        result=run(request,os.environ.get('BAOFANG_AI_TASK_KEY',''),poll_limit=20)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
        return 0
    except Exception as exc:
        # Our validation errors contain labels only. Never print transport
        # payloads, headers, tracebacks or arbitrary exception text.
        message=str(exc) if isinstance(exc,ValueError) else 'AI任务未完成，原歌词保持不变；请检查配置或网络'
        secret=os.environ.get('BAOFANG_AI_TASK_KEY','')
        if secret:
            message=message.replace(secret,'[密钥]')
        print(json.dumps({'phase':'failed','detail':message[:500]},ensure_ascii=False),flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
