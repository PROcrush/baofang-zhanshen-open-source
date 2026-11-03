"""Bailian file ASR: private temporary upload, durable submit, bounded polling.

API keys are sent only to the selected official Beijing API host. OSS receives
its own upload policy; result downloads never receive the user's API key.
"""
import fcntl
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from worker.ai_providers import HTTPTransport, ProviderError, credential, profile, speech_result
from worker.cloud_pipeline import _atomic, _read_json, _verify_source, preflight

MAX_AUDIO_BYTES=50*1024*1024
AUDIO_SUFFIXES={'.wav','.mp3','.ogg','.m4a','.flac','.aac','.wma','.mp4'}
ASR_MODELS={'fun-asr','fun-asr-mtl','fun-asr-2025-11-07','fun-asr-2025-08-25',
            'fun-asr-mtl-2025-08-25','qwen-audio-3.0-asr-flash-filetrans',
            'qwen-audio-3.1-asr-flash-filetrans'}


def config(value):
    clean=profile('bailian-asr',value['baseUrl'],value['model'])
    if clean['model'] not in ASR_MODELS:
        raise ValueError('请选择百炼文件识别模型；实时和极速模型使用不同接口')
    return clean


def _oss_url(value,role):
    if not isinstance(value,str) or len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ProviderError('百炼临时存储地址无效，已停止传输')
    parsed=urlsplit(value)
    host=re.fullmatch(r'dashscope-'+role+r'-[a-z0-9-]+\.oss-cn-beijing\.aliyuncs\.com',parsed.hostname or '')
    if (parsed.scheme!='https' or not host or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.fragment
            or (role=='file' and (parsed.query or parsed.path not in ('','/')))):
        raise ProviderError('百炼返回了非北京地域官方临时存储地址，已停止传输')
    return value


def upload_policy(clean,key,transport):
    url=clean['baseUrl']+'/api/v1/uploads?action=getPolicy&model='+quote(clean['model'],safe='')
    payload,_=transport.request(url,{'Authorization':'Bearer '+credential(key),'Content-Type':'application/json'},method='GET',timeout=30)
    data=payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(data,dict):
        raise ProviderError('百炼未返回上传权限，请检查通用Key、地域和模型权限')
    _oss_url(data.get('upload_host'),'file')
    for field in ('policy','signature','oss_access_key_id'):
        value=data.get(field)
        if not isinstance(value,str) or not 1<=len(value)<=20000 or any(ord(c)<33 or ord(c)>126 for c in value):
            raise ProviderError('百炼上传凭证无效，已停止传输')
    directory=data.get('upload_dir','')
    if (not isinstance(directory,str) or not re.fullmatch(r'dashscope-instant/[A-Za-z0-9._/-]{1,500}',directory)
            or '..' in directory.split('/')):
        raise ProviderError('百炼上传目录无效，已停止传输')
    if data.get('x_oss_object_acl')!='private' or str(data.get('x_oss_forbid_overwrite')).lower()!='true':
        raise ProviderError('百炼上传权限未确认私有且禁止覆盖，已停止传输')
    return data


def _upload(clean,key,source,transport):
    data=upload_policy(clean,key,transport)
    size=source.stat().st_size
    limit=data.get('max_file_size_mb')
    if isinstance(limit,bool) or not isinstance(limit,(int,float)) or size>limit*1024*1024:
        raise ProviderError('歌曲超过百炼本次上传大小限制，请先转换为较小的音频')
    name='audio-'+uuid.uuid4().hex+source.suffix.lower()
    object_key=data['upload_dir'].rstrip('/')+'/'+name
    fields={'OSSAccessKeyId':data['oss_access_key_id'],'Signature':data['signature'],'policy':data['policy'],
            'x-oss-object-acl':'private','x-oss-forbid-overwrite':'true','key':object_key,'success_action_status':'200'}
    boundary='baofang-'+uuid.uuid4().hex
    parts=[('--'+boundary+'\r\nContent-Disposition: form-data; name="'+field+'"\r\n\r\n'+value+'\r\n').encode('utf-8') for field,value in fields.items()]
    audio=source.read_bytes()
    if len(audio)!=size or not 0<size<=MAX_AUDIO_BYTES:
        raise ValueError('歌曲在读取期间发生变化，已停止上传')
    parts.extend([('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('ascii'),audio,('\r\n--'+boundary+'--\r\n').encode('ascii')])
    transport.request(data['upload_host'],{'Content-Type':'multipart/form-data; boundary='+boundary},
                      b''.join(parts),timeout=120,expect_json=False)
    return 'oss://'+object_key


def _output(receipt,phase,detail):
    return {'phase':phase,'detail':detail,'taskId':receipt.get('taskId','')}


def run_job(plan,receipt_path,*,action,key,transport=None,poll_limit=1):
    clean=config(plan['config'])
    transport=transport or HTTPTransport()
    if action not in ('recognize','query'):
        raise ValueError('百炼识别任务类型无效')
    if not 1<=poll_limit<=20:
        raise ValueError('百炼查询次数无效')
    source=Path(plan['sourcePath'])
    if source.suffix.lower() not in AUDIO_SUFFIXES or not 0<plan['size']<=MAX_AUDIO_BYTES:
        raise ValueError('百炼识别支持50MB以内的常见音频或MP4文件')
    receipt_path=Path(receipt_path)
    receipt_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(receipt_path.with_name(receipt_path.name+'.lock'),os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('百炼原任务仍在处理，请稍后续查') from exc
        receipt=_read_json(receipt_path) if receipt_path.exists() else None
        if receipt:
            if receipt.get('versionId')!=plan['versionId'] or receipt.get('config')!=clean:
                raise ValueError('百炼回执与音频版本或原服务配置不一致')
            # A resumed file may have moved or acquired a new ctime while its
            # bytes stayed identical. Rehash it fully before refreshing only
            # its filesystem proof; a filename/stat match is never sufficient.
            current=preflight({'path':str(source),'versionId':plan['versionId']},plan['host'],0)
            if current['size']!=plan['size'] or abs(current['durationMs']-plan['durationMs'])>0.001:
                raise ValueError('原任务音频时长或大小已变化，请重新核验')
            plan=dict(plan,identityProof=current['identityProof'])
            if receipt.get('state')=='ready':
                payload=_read_json(receipt_path.with_name('result.json'),limit=4*1024*1024)
                proposal=speech_result(payload,clean,plan['versionId'],plan['durationMs'])
                return dict(_output(receipt,'review','已恢复百炼原识别结果，审阅后应用'),proposal=proposal)
            if receipt.get('state') in ('uploading','upload_failed','quota_blocked'):
                if action=='query':
                    detail='原识别请求被额度限制拒绝；额度恢复后请重新预检并确认上传' if receipt['state']=='quota_blocked' else '原任务尚未提交识别，请重新预检并确认上传；不会通过续查新增识别'
                    return _output(receipt,'upload_required',detail)
                receipt=None
            elif not receipt.get('taskId'):
                return _output(receipt,'unknown','原提交状态不确定，未重复上传或识别；请到百炼控制台核对任务')
        else:
            _verify_source(dict(plan,maxCostPTC=0))
        if not receipt and action=='query':
            raise ValueError('没有百炼提交回执，不会通过续查新增上传')
        if not receipt:
            receipt={'schemaVersion':1,'versionId':plan['versionId'],'config':clean,'state':'uploading','planId':plan['planId']}
            _atomic(receipt_path,receipt)
            try:
                resource=_upload(clean,key,source,transport)
                _verify_source(dict(plan,maxCostPTC=0))
            except (OSError,ValueError):
                receipt['state']='upload_failed'
                _atomic(receipt_path,receipt)
                raise
            # Save intent before the billable POST. An ambiguous response is never resubmitted.
            receipt['state']='submit_unknown'
            _atomic(receipt_path,receipt)
            headers={'Authorization':'Bearer '+credential(key),'Content-Type':'application/json',
                     'X-DashScope-Async':'enable','X-DashScope-OssResourceResolve':'enable'}
            body={'model':clean['model'],'input':{'file_urls':[resource]},'parameters':{'channel_id':[0]}}
            try:
                payload,_=transport.request(clean['baseUrl']+'/api/v1/services/audio/asr/transcription',headers,
                                            json.dumps(body).encode('utf-8'),timeout=90)
            except ProviderError as exc:
                if exc.http_status==403 and exc.code=='AllocationQuota.FreeTierOnly':
                    receipt.update(state='quota_blocked',errorCode=exc.code)
                    _atomic(receipt_path,receipt)
                raise
            output=payload.get('output',{}) if isinstance(payload,dict) else {}
            task_id=output.get('task_id','')
            if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',task_id):
                return _output(receipt,'unknown','百炼未返回可核验的任务ID，请核对控制台；未自动重新提交')
            receipt.update(taskId=task_id,state='polling')
            _atomic(receipt_path,receipt)
        for attempt in range(poll_limit):
            payload,_=transport.request(clean['baseUrl']+'/api/v1/tasks/'+receipt['taskId'],
                {'Authorization':'Bearer '+credential(key)},method='GET',timeout=30)
            output=payload.get('output',{}) if isinstance(payload,dict) else {}
            if output.get('task_id')!=receipt['taskId']:
                raise ProviderError('百炼查询结果与原任务ID不一致')
            status=output.get('task_status')
            if status in ('PENDING','RUNNING'):
                if attempt+1<poll_limit:
                    time.sleep(3)
                    continue
                return _output(receipt,'polling','百炼正在识别，可继续查询原任务；不会重复上传或计费提交')
            if status in ('FAILED','CANCELED','CANCELLED'):
                receipt['state']='failed'
                _atomic(receipt_path,receipt)
                return _output(receipt,'failed','百炼原识别任务未成功，请检查模型权限、额度和音频格式')
            results=output.get('results')
            if status!='SUCCEEDED' or not isinstance(results,list) or len(results)!=1 or results[0].get('subtask_status')!='SUCCEEDED':
                raise ProviderError('百炼没有确认所选音频识别成功，请核对原任务')
            url=_oss_url(results[0].get('transcription_url'),'result')
            payload,_=transport.request(url,{},method='GET',timeout=30)
            proposal=speech_result(payload,clean,plan['versionId'],plan['durationMs'])
            _verify_source(dict(plan,maxCostPTC=0))
            _atomic(receipt_path.with_name('result.json'),payload)
            receipt['state']='ready'
            _atomic(receipt_path,receipt)
            return dict(_output(receipt,'review','百炼已返回歌词和真实时间码，审阅后应用'),proposal=proposal)
    finally:
        os.close(descriptor)


def receipt_path(runtime, config, version):
    """Stable identity shared by studio, decks and library, including old receipts."""
    import hashlib
    payload = [{'config': config, 'versionId': version}]
    identity = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()
    return Path(runtime) / 'bailian-jobs' / identity / 'receipt.json'
