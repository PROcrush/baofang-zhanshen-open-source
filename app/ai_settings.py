"""Model configuration and reviewed AI proposals, separate from the live clock."""
import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Property, QProcess, QProcessEnvironment, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from app.preparation import _atomic, _proof_current
from app.storage import validate_timeline
from app.credentials import KeychainStore
from worker.ai_providers import PROVIDERS, credential, profile


# Official public destinations, checked against Aliyun's Chinese documentation.
# Browser navigation never includes the user's credentials or song information.
BAILIAN_PORTALS = {
    'activate': 'https://bailian.console.aliyun.com/cn-beijing/model/market',
    'key': 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key',
    'pricing': 'https://help.aliyun.com/zh/model-studio/model-pricing',
}


def rows_digest(rows):
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()


def studio_rows(timeline):
    return [dict(startMs=row['startMs'],endMs=row['endMs'],text=row['text'],
                 **({'characters':copy.deepcopy(row['words']),'timingQuality':'word'} if row.get('words') else {'timingQuality':'sentence'})) for row in timeline['segments']]


class AISettings(QObject):
    stateChanged=Signal()

    def __init__(self,root,runtime,controller,studio,*,process_factory=None,credential_store=None):
        super().__init__()
        self.root,self.runtime=Path(root),Path(runtime)
        self.controller,self.studio=controller,studio
        self.process_factory=process_factory or QProcess
        self.profiles={}
        self.active={'speech':'bailian-asr','text':'qwen'}
        self.keys={}
        self.credential_store=credential_store or KeychainStore()
        self.remembered_hosts=set()
        self.keychain_notice=''
        self.results={}
        self.portal_notice=''
        self.portal_fallback_url=''
        self.process=None
        self.current=None
        self.pending=None
        self.reference_session=None
        self.plan=None
        self.query_request=None
        self.detail='百炼负责识别实际歌曲；原版歌词匹配无需额外Key。'
        self.phase='idle'
        self.scope='studio'
        self._closed=False
        self._proposal_stale=False
        try:
            data=json.loads((self.runtime/'ai-profiles.json').read_text())
            for host in data.get('rememberedBailianHosts',[]):
                clean=profile('bailian-asr',host,'fun-asr')
                if self._key_slot(clean)[1]==host:
                    self.remembered_hosts.add(host)
            for kind,provider in data.get('active',{}).items():
                if kind in self.active and provider in PROVIDERS and PROVIDERS[provider]['kind']==kind:
                    self.active[kind]=provider
            for provider,value in data.get('profiles',{}).items():
                if provider in PROVIDERS:
                    self.profiles[provider]=profile(provider,value.get('baseUrl'),value.get('model'),budget=value.get('budgetPTC',0))
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        try:
            request=json.loads((self.runtime/'bailian-resume.json').read_text())
            clean=profile('bailian-asr',request['config']['baseUrl'],request['config']['model'])
            receipt=Path(request['receipt']).resolve()
            receipt.relative_to((self.runtime/'bailian-jobs').resolve())
            if request['config']!=clean or request['plan']['config']!=clean:
                raise ValueError('恢复配置无效')
            self.query_request=request
            self.detail='发现百炼原任务；填入原服务Key后可继续查询，不重复上传。'
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            pass
        for host in self.remembered_hosts:
            try:
                saved=self.credential_store.read(host)
                if saved:
                    self.keys[('bailian',host)]=credential(saved)
                else:
                    self.keychain_notice='钥匙串中未找到已保存的 Key，请重新填写。'
            except (OSError,ValueError):
                self.keychain_notice='暂时无法读取系统钥匙串；请解锁后重新打开应用，或填写 Key 仅本次使用。'
        try:
            self._sync_preparation()
        except (OSError,ValueError,TypeError):
            self.detail='已保存的识别配置暂不可用，请重新选择百炼模型并应用配置。'
            self.phase='failed'
        self._last_offline=self._offline()
        self._last_target=self._target()
        controller.stateChanged.connect(self._offline_changed)
        if hasattr(studio,'stateChanged'):
            studio.stateChanged.connect(self._studio_changed)

    def _sync_preparation(self):
        manager=getattr(self.controller,'preparation',None)
        config=self._config('speech')
        if config['provider']=='bailian-asr' and hasattr(manager,'configure_bailian'):
            manager.configure_bailian(config,self.keys.get(self._key_slot(config),''))
        elif config['provider']=='302-singing' and self.keys.get(self._key_slot(config)):
            manager.configure_inline(self.keys[self._key_slot(config)],'domestic' if config['baseUrl']=='https://api.302ai.cn' else 'international',config.get('budgetPTC',0))

    def _preparation_idle(self):
        manager=getattr(self.controller,'preparation',None)
        if getattr(manager,'processes',{}) or (getattr(manager,'batch',None) or {}).get('status')=='running':
            raise ValueError('请先暂停批次并等待当前歌曲处理完成，再修改或清除 Key')

    def _target(self):
        if self.scope=='studio':
            state=getattr(self.studio,'_state',getattr(self.studio,'value',{}))
            audio=self.studio._sources.get('audio') or {}
            return audio.get('sha256',''),state.get('audioTitle','尚未导入歌曲')
        deck=getattr(self.controller,'_state',{}).get('deck'+self.scope,{})
        return deck.get('versionId',''),deck.get('title','等待'+self.scope+'盘歌曲')

    def _studio_changed(self):
        if self.pending and self.scope=='studio':
            current=getattr(self.studio,'_state',{})
            self._proposal_stale=rows_digest(current.get('segments',[]))!=self.pending['source']['snapshot']
        self._target_changed()

    def _target_changed(self):
        target=self._target()
        changed=target!=self._last_target
        self._last_target=target
        if changed:
            self.plan=None
            self.reference_session=None
            if self.pending:
                self._proposal_stale=True
        if changed or self.pending:
            self.stateChanged.emit()

    def _config(self,kind):
        provider=self.active[kind]
        preset=PROVIDERS[provider]
        return dict(self.profiles.get(provider,{'provider':provider,'kind':kind,'baseUrl':preset['baseUrl'],'model':preset['model'],'budgetPTC':0}))

    @staticmethod
    def _key_slot(config):
        if config['provider'] in ('bailian-asr','qwen'):
            parsed=urlsplit(config['baseUrl'])
            authority=parsed.hostname+(':'+str(parsed.port) if parsed.port not in (None,443) else '')
            return 'bailian','https://'+authority
        return config['provider'],config['baseUrl']

    @staticmethod
    def _result_slot(config):
        return config['provider'],config['baseUrl'],config['model']

    def _bailian_configs(self):
        speech=dict(self.profiles.get('bailian-asr',profile('bailian-asr')))
        text=dict(self.profiles.get('qwen',profile('qwen')))
        if self._key_slot(speech)!=self._key_slot(text):
            text=profile('qwen',speech['baseUrl']+'/compatible-mode/v1',text['model'])
        return speech,text

    def _offline(self):
        return bool(getattr(getattr(self.controller,'preparation',None),'offline_only',False))

    @Property('QVariantMap',notify=stateChanged)
    def state(self):
        values={}
        for kind in ('speech','text'):
            config=self._config(kind)
            config.update(label=PROVIDERS[config['provider']]['label'],hint=PROVIDERS[config['provider']]['hint'],
                          docs=PROVIDERS[config['provider']]['docs'],keyReady=bool(self.keys.get(self._key_slot(config))),
                          result=self.results.get(self._result_slot(config),'尚未验证'))
            config['remembered']=config['provider'] in ('bailian-asr','qwen') and self._key_slot(config)[1] in self.remembered_hosts
            values[kind]=config
        pending=self.pending or {}
        proposal=pending.get('proposal',{})
        session=self.reference_session or {}
        speech,text=self._bailian_configs()
        using_bailian=(self.active=={'speech':'bailian-asr','text':'qwen'}
                       and self._key_slot(self._config('speech'))==self._key_slot(self._config('text')))
        return {'providers':[dict(id=key,**value) for key,value in PROVIDERS.items()],**values,
                'usingBailian':using_bailian,'bailian':{'baseUrl':speech['baseUrl'],'speechModel':speech['model'],
                    'textModel':text['model'],'remembered':self._key_slot(speech)[1] in self.remembered_hosts,
                    'keychainNotice':self.keychain_notice,'keyReady':bool(self.keys.get(self._key_slot(speech))),
                    'speechResult':self.results.get(self._result_slot(speech),'尚未验证'),
                    'textResult':self.results.get(self._result_slot(text),'尚未验证')},
                'portalNotice':self.portal_notice,'portalFallbackUrl':self.portal_fallback_url,
                'busy':self.process is not None,'offline':self._offline(),'detail':self.detail,'phase':self.phase,'scope':self.scope,
                'plan':copy.deepcopy(self.plan or {}),'canQuery':bool(self.query_request),'targetTitle':self._target()[1],
                'proposalKind':pending.get('kind',''),'changes':copy.deepcopy(proposal.get('changes',[])),
                'previewText':proposal.get('text','') or '\n'.join(row.get('text','') for row in proposal.get('rows',proposal.get('timeline',{}).get('segments',[]))),
                'canApply':bool(self.pending and not self._proposal_stale and (proposal.get('timeline') or any(row.get('selected') for row in proposal.get('changes',[])))),
                'proposalStale':self._proposal_stale,
                'referenceQuery':session.get('title') or self._reference_title(),
                'referenceArtist':session.get('artist',''),
                'references':copy.deepcopy(session.get('candidates',[])),
                'referenceCached':session.get('cached',False),
                'reference':copy.deepcopy(proposal.get('reference',{})),
                'referenceCoverage':proposal.get('coverage',0),
                'unresolved':copy.deepcopy(proposal.get('unresolved',[])),
                'referenceProposal':proposal.get('method')=='reference_text_pinyin',
                'hasProposal':bool(self.pending)}

    def _reference_title(self):
        from worker.lyric_reference import guess_title
        return guess_title(self._target()[1])

    def _save(self):
        _atomic(self.runtime/'ai-profiles.json',{'schemaVersion':1,'active':self.active,'profiles':self.profiles,'rememberedBailianHosts':sorted(self.remembered_hosts)})

    @Slot(str, result=bool)
    def openBailianPortal(self, destination):
        url=BAILIAN_PORTALS.get(destination)
        if not url:
            return False
        opened=QDesktopServices.openUrl(QUrl(url))
        self.portal_fallback_url='' if opened else url
        self.portal_notice=('已请求浏览器打开阿里云官网，完成后回到这里填写 Key。'
                            if opened else '浏览器未能打开，请复制下方地址到浏览器。')
        self.stateChanged.emit()
        return opened

    def _notice(self,message,phase='failed'):
        self.detail=str(message)[:1000]
        for key in self.keys.values():
            self.detail=self.detail.replace(key,'[密钥]')
        self.phase=phase
        self.stateChanged.emit()

    @Slot(str,str)
    def selectProvider(self,kind,provider):
        if kind not in self.active or provider not in PROVIDERS or PROVIDERS[provider]['kind']!=kind:
            return
        previous=dict(self.active)
        try:
            if self.process:
                raise ValueError('请先等待当前 AI 任务完成')
            self._preparation_idle()
            self.active[kind]=provider
            if kind=='speech':
                self._sync_preparation()
            self.plan=None
            self._save()
            self.stateChanged.emit()
        except (OSError,ValueError) as exc:
            self.active=previous
            self._notice(exc)

    @Slot(str,str,str,str,str,float,result=bool)
    def configure(self,kind,provider,base,model,key,budget=0):
        try:
            self._preparation_idle()
            clean=profile(provider,base,model,budget=budget)
            if clean['kind']!=kind:
                raise ValueError('服务类型与所选功能不一致')
            if provider=='bailian-asr':
                from worker.bailian import config as bailian_config
                bailian_config(clean)
            slot=self._key_slot(clean)
            secret=credential(key) if key else self.keys.get(slot,'')
            if not secret:
                raise ValueError('请填写这个服务商和地址对应的API Key')
            if provider in ('bailian-asr','qwen'):
                self._store_bailian_secret(slot[1],secret,slot[1] in self.remembered_hosts)
            # Credentials never enter JSON/state.
            profiles=dict(self.profiles,**{provider:clean})
            active=dict(self.active,**{kind:provider})
            _atomic(self.runtime/'ai-profiles.json',{'schemaVersion':1,'active':active,'profiles':profiles,'rememberedBailianHosts':sorted(self.remembered_hosts)})
            self.profiles,self.active=profiles,active
            self.keys[slot]=secret
            self.results[self._result_slot(clean)]='配置已保存，尚未验证模型'
            self.plan=None
            if provider=='302-singing':
                self.controller.preparation.configure_inline(secret,'domestic' if clean['baseUrl']=='https://api.302ai.cn' else 'international',clean['budgetPTC'])
            if provider in ('bailian-asr','qwen'):
                self._sync_preparation()
            storage=('Key 已更新到系统钥匙串。' if slot[0]=='bailian' and slot[1] in self.remembered_hosts and not self.keychain_notice else 'Key 仅在本次运行使用。')
            self._notice('配置已保存；'+storage+(self.keychain_notice if slot[0]=='bailian' else ''),'configured')
            return True
        except (OSError,ValueError,TypeError) as exc:
            self._notice(exc)
            return False

    def _store_bailian_secret(self,host,secret,remember):
        self.keychain_notice=''
        try:
            if remember:
                self.credential_store.write(host,secret)
                self.remembered_hosts.add(host)
            elif host in self.remembered_hosts:
                self.credential_store.delete(host)
                self.remembered_hosts.remove(host)
        except (OSError,ValueError):
            self.keychain_notice='钥匙串操作未完成，保存状态未改变；当前填写的 Key 仍可在本次运行使用。'

    @Slot(str,str,str,str,result=bool)
    @Slot(str,str,str,str,bool,result=bool)
    def configureBailian(self,key,base,speech_model,text_model,remember=None):
        try:
            if self.process:
                raise ValueError('请等待或取消当前AI任务后修改百炼配置')
            self._preparation_idle()
            from worker.bailian import config as bailian_config
            speech=bailian_config(profile('bailian-asr',base,speech_model))
            text=profile('qwen',speech['baseUrl']+'/compatible-mode/v1',text_model)
            secret=credential(key) if key else self.keys.get(self._key_slot(speech),'')
            if not secret:
                raise ValueError('请填写百炼北京地域的通用API Key')
            if secret.startswith('sk-sp-'):
                raise ValueError('请使用百炼通用Key，套餐专属Key不适用于这条语音识别路线')
            host=self._key_slot(speech)[1]
            remember=(host in self.remembered_hosts) if remember is None else remember
            self._store_bailian_secret(host,secret,remember)
            profiles=dict(self.profiles,**{'bailian-asr':speech,'qwen':text})
            active={'speech':'bailian-asr','text':'qwen'}
            _atomic(self.runtime/'ai-profiles.json',{'schemaVersion':2,'active':active,'profiles':profiles,'rememberedBailianHosts':sorted(self.remembered_hosts)})
            self.profiles,self.active=profiles,active
            self.keys[self._key_slot(speech)]=secret
            for value in (speech,text):
                self.results[self._result_slot(value)]='Key已配置，尚未验证'
            self.plan=None
            self._sync_preparation()
            storage='已记住在系统钥匙串。' if remember and not self.keychain_notice else '本次运行可用。'
            self._notice('百炼已配置：单首、批量和文字检查共用一个 Key；'+storage,'configured')
            return True
        except (OSError,ValueError,TypeError) as exc:
            self._notice(exc)
            return False

    @Slot()
    def testBailian(self):
        try:
            if not self.state['usingBailian']:
                raise ValueError('先保存百炼统一配置')
            speech=self._ready('speech')
            text=self._ready('text')
            self._spawn({'action':'test-bailian','config':text,'speechConfig':speech})
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot()
    def clearBailianKey(self):
        try:
            if self.process:
                raise ValueError('请等待或取消当前 AI 任务后清除 Key')
            self._preparation_idle()
            for host in list(self.remembered_hosts):
                self.credential_store.delete(host)
                self.remembered_hosts.remove(host)
                self._save()
            for slot in list(self.keys):
                if slot[0]=='bailian':
                    self.keys.pop(slot)
            for slot in list(self.results):
                if slot[0] in ('bailian-asr','qwen'):
                    self.results.pop(slot)
            self.keychain_notice=''
            self.plan=None
            self._sync_preparation()
            self._notice('已清除百炼共用 Key，并删除本应用记住的钥匙串项。','idle')
        except (OSError,ValueError):
            self._notice('暂未清除 Key：请先结束识别任务并确认登录钥匙串已解锁，再重试。')

    @Slot(str)
    def clearKey(self,kind):
        config=self._config(kind)
        if config['provider'] in ('bailian-asr','qwen'):
            self.clearBailianKey()
            return
        self.keys.pop(self._key_slot(config),None)
        self.results.pop(self._result_slot(config),None)
        self.plan=None
        if config['provider']=='302-singing':
            self.controller.preparation.clear_key()
        self._notice('已清除该服务商的本次运行密钥。','idle')

    @Slot()
    def import302(self):
        path,_=QFileDialog.getOpenFileName(None,'导入包房战神302配置','','服务配置 (*.json)')
        if not path:
            return
        try:
            manager=self.controller.preparation
            manager.configure_file(path)
            self.configure('speech','302-singing','https://api.302ai.cn' if manager.route=='domestic' else 'https://api.302.ai','doubao-vc-singing',manager._key,manager.budget)
        except (OSError,ValueError,TypeError) as exc:
            self._notice(exc)

    def _ready(self,kind):
        if self._offline():
            raise ValueError('离线演出已开启，云端测试、上传与文字纠错已暂停')
        config=self._config(kind)
        clean=profile(config['provider'],config['baseUrl'],config['model'],budget=config.get('budgetPTC',0))
        if not self.keys.get(self._key_slot(clean)):
            raise ValueError('先保存所选服务商的Key与模型配置')
        return clean

    @Slot(str)
    def test(self,kind):
        try:
            config=self._ready(kind)
            self._spawn({'action':'test','config':config})
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot(str)
    def setScope(self,scope):
        if scope in ('studio','A','B') and not self.process:
            self.scope=scope
            self.plan=None
            self.pending=None
            self.reference_session=None
            self._proposal_stale=False
            self.query_request=None
            self.stateChanged.emit()

    def _source(self):
        if self.scope=='studio':
            state=self.studio.state
            if state['busy'] or not self.studio._sources['audio']:
                raise ValueError('先在制作页导入歌曲，并等待本地任务完成')
            identity=self.studio._sources['audio']
            track={'path':identity['path'],'versionId':'sha256:'+identity['sha256'],'durationMs':state['durationMs'],'title':state['audioTitle']}
            rows=state['segments']
            timeline=None
        else:
            track=self.controller.recognition_track(self.scope)
            timeline=self.controller.timelines.get(track['versionId'])
            rows=copy.deepcopy(timeline['segments']) if timeline else []
        return {'track':copy.deepcopy(track),'rows':copy.deepcopy(rows),'timeline':copy.deepcopy(timeline),
                'snapshot':rows_digest(rows),'scope':self.scope}

    @Slot()
    def planSpeech(self):
        try:
            config=self._ready('speech')
            source=self._source()
            self.pending=None
            self.plan=None
            self.query_request=None
            self._spawn(dict(source,action='plan',config=config,planId=uuid.uuid4().hex))
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot()
    def startSpeech(self):
        try:
            config=self._ready('speech')
            plan=self.plan
            source=self._source()
            if not plan or plan['config']!=config or plan['scope']!=self.scope or plan['versionId']!=source['track']['versionId'] or plan['snapshot']!=source['snapshot']:
                raise ValueError('歌曲、歌词或模型已改变，请重新预检并审阅计划')
            if config['provider']=='302-singing' and not plan['budgetAllowed']:
                raise ValueError('302单次预算不足，请调整预算后重新预检')
            if config['provider']=='302-singing':
                legacy=self.controller.preparation
                version=plan['versionId']
                if version in legacy.processes or legacy._batch_network_conflict(version):
                    raise ValueError('歌曲准备已有任务，请先完成或暂停当前任务')
                if not legacy._job(version):
                    if version in legacy.reservations:
                        raise ValueError('该歌曲已有提交预留记录，请先核对原任务，不重复提交')
                    if legacy._remaining_budget()<plan['estimatedCostPTC']:
                        raise ValueError('302本机剩余额度不足，不新增识别')
                    legacy.reservations[version]=plan['estimatedCostPTC']
                    _atomic(legacy._receipt(version).with_name('reservation.json'),{'versionId':version,'reservedCostPTC':plan['estimatedCostPTC'],'intentId':plan['planId']})
                    plan=dict(plan,sessionRemainingPTC=legacy._remaining_budget()+plan['estimatedCostPTC'])
            if config['provider']=='bailian-asr':
                from worker.bailian import receipt_path
                receipt=receipt_path(self.runtime,config,plan['versionId'])
                manager=self.controller.preparation
                if plan['versionId'] in getattr(manager,'processes',{}):
                    raise ValueError('批量入口正在处理同一歌曲，请等待当前任务完成')
                if receipt.with_name('reservation.json').exists() and not receipt.exists():
                    raise ValueError('该歌曲已有提交预留，尚无可核对回执；请先核对原任务，避免重复提交')
            else:
                receipt=self.runtime/'cloud-jobs'/plan['sha256']/'receipt.json' if config['provider']=='302-singing' else self.runtime/'ai-jobs'/plan['planId']/'receipt.json'
            receipt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            request=dict(source,action='recognize',config=config,plan=copy.deepcopy(plan),receipt=str(receipt))
            if config['provider']=='bailian-asr':
                resume=receipt.with_name('source.json')
                status=json.loads(receipt.read_text()).get('state') if receipt.exists() else None
                if resume.exists() and status not in (None,'uploading','upload_failed','quota_blocked'):
                    request=json.loads(resume.read_text())
                    if 'snapshot' not in request:
                        request.update(source)
                    request['receipt']=str(receipt)
                    request['action']='query'
                    # Same SHA may move to a new path. Reuse the current proof,
                    # while retaining the original lyric snapshot for review.
                    request['plan']=copy.deepcopy(plan)
                    request['track']=copy.deepcopy(source['track'])
                else:
                    _atomic(resume,request)
                _atomic(self.runtime/'bailian-resume.json',request)
                self.query_request=copy.deepcopy(request)
            self.plan=None
            self._spawn(request)
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot()
    def querySpeech(self):
        try:
            request=copy.deepcopy(self.query_request)
            if not request or self._offline() or not self.keys.get(self._key_slot(request['config'])):
                raise ValueError('暂无可续查任务或缺少原服务商的Key')
            request['action']='query'
            self._spawn(request)
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot()
    def correctLyrics(self):
        try:
            config=self._ready('text')
            source=self._source()
            if not source['rows']:
                raise ValueError('先导入或识别出带时间码的歌词，再使用文字纠错')
            self.pending=None
            self._spawn(dict(source,action='correct',config=config))
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot(str,str)
    def searchReference(self,title,artist):
        try:
            source=self._source()
            if not source['rows']:
                raise ValueError('先导入或识别出带时间码的歌词，再搜索原版歌词')
            self.pending=None
            self.reference_session=None
            self._proposal_stale=False
            self._spawn(dict(source,action='search-reference',title=title,artist=artist,
                             cacheDirectory=str(self.runtime/'reference-cache'),offline=self._offline()))
        except (ValueError,OSError) as exc:
            self._notice(exc)

    @Slot(str)
    def matchReference(self,identity):
        try:
            session=self.reference_session
            if not session:
                raise ValueError('先搜索原版歌词')
            source=self._source()
            original=session['source']
            if source['snapshot']!=original['snapshot'] or source['track']['versionId']!=original['track']['versionId'] or source['scope']!=original['scope']:
                raise ValueError('歌词或歌曲已经变化，请重新搜索匹配')
            reference=next((row for row in session['candidates'] if row['id']==identity),None)
            if reference is None:
                raise ValueError('所选参考歌词不存在')
            self.pending=None
            self._spawn(dict(source,action='match-reference',reference=copy.deepcopy(reference),offline=True))
        except (ValueError,OSError) as exc:
            self._notice(exc)

    def _spawn(self,request):
        if self.process:
            raise ValueError('已有AI任务在处理，请等待或取消')
        local_reference=request['action']=='match-reference' or (request['action']=='search-reference' and request.get('offline'))
        if request['action']!='plan' and not local_reference and self._offline():
            raise ValueError('离线模式不发送云端请求')
        directory=self.runtime/'ai-jobs'/uuid.uuid4().hex
        directory.mkdir(parents=True,mode=0o700)
        path=directory/'request.json'
        _atomic(path,request)
        environment=QProcessEnvironment()
        system=QProcessEnvironment.systemEnvironment()
        for name in ('PATH','TMPDIR','LANG','LC_ALL','LC_CTYPE','BAOFANG_FFMPEG','FFPROBE'):
            if system.contains(name):
                environment.insert(name,system.value(name))
        environment.insert('PYTHONDONTWRITEBYTECODE','1')
        environment.insert('PYTHONNOUSERSITE','1')
        model_request=request['action'] not in ('plan','search-reference','match-reference')
        environment.insert('BAOFANG_AI_TASK_KEY',self.keys.get(self._key_slot(request['config']),'') if model_request else '')
        process=self.process_factory(self)
        process.setWorkingDirectory(str(self.root))
        process.setProcessEnvironment(environment)
        self.process=process
        self.current={'request':request,'output':bytearray(),'directory':directory}
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(lambda _:self._start_failed(process))
        self.phase='working'
        self.detail={'plan':'正在本机核验歌曲，尚未上传','test':'正在验证所选模型',
            'test-bailian':'正在核验百炼上传权限和文字模型连接，不上传歌曲',
            'correct':'正在生成纠错建议，只发送歌词文字','recognize':'正在上传所审阅的音频并识别',
            'search-reference':'正在搜索原版歌词并核对识别文字；仅发送歌名与歌手' if not request.get('offline') else '正在读取本地原版歌词缓存并匹配',
            'match-reference':'正在本机匹配参考歌词，保留实际歌曲时间码',
            'query':'正在查询原识别任务，不新增上传'}[request['action']]
        process.start(sys.executable,['-m','worker.ai_tasks','--request',str(path)])
        self.stateChanged.emit()

    def _read(self):
        if not self.process:
            return
        self.current['output'].extend(bytes(self.process.readAllStandardOutput()))
        if len(self.current['output'])>6_000_000:
            self.cancel()
            self._notice('后台结果超过大小界限，原歌词保持不变')

    def _start_failed(self,process):
        if self.process is process and process.state()==QProcess.ProcessState.NotRunning:
            self._finished(-1)

    def _finished(self,code,*_):
        if not self.process:
            return
        self._read()
        if not self.process:
            return
        process,self.process=self.process,None
        task,self.current=self.current,None
        process.deleteLater()
        try:
            output=json.loads(bytes(task['output']).splitlines()[-1])
            if code!=0 or output.get('phase')=='failed':
                raise ValueError(output.get('detail','AI任务未完成'))
            request=task['request']
            _atomic(task['directory']/'result.json',output)
            if output.get('plan'):
                if request['config']!=self._config('speech'):
                    raise ValueError('识别配置已改变，请重新预检')
                self.plan=output['plan']
            if request['action']=='test':
                self.results[self._result_slot(request['config'])]=output['detail']
            if request['action']=='test-bailian':
                for kind,value in output.get('capabilities',{}).items():
                    config=request['speechConfig'] if kind=='speech' else request['config']
                    self.results[self._result_slot(config)]=value
            if output.get('proposal'):
                self.pending={'kind':'correct' if request['action'] in ('correct','search-reference','match-reference') else 'speech','source':request,'proposal':output['proposal']}
                self._proposal_stale=False
                try:
                    source=self._source()
                    self._proposal_stale=source['scope']!=request['scope'] or source['track']['versionId']!=request['track']['versionId'] or source['snapshot']!=request['snapshot']
                except (ValueError,OSError):
                    self._proposal_stale=True
            if request['action']=='search-reference':
                self.reference_session={'source':request,'title':request['title'],'artist':request['artist'],
                                        'candidates':output.get('candidates',[]),'cached':output.get('cached',False)}
            if output.get('phase') in ('polling','queued','unknown') and request.get('config',{}).get('provider') in ('302-singing','bailian-asr'):
                self.query_request=request
            elif request['action'] in ('recognize','query'):
                self.query_request=None
            self._notice(output.get('detail','任务完成'),output.get('phase','ready'))
        except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
            self._notice(exc)

    @Slot(int,bool)
    def selectChange(self,index,selected):
        if self.pending and self.pending['kind']=='correct':
            for row in self.pending['proposal']['changes']:
                if row['index']==index:
                    row['selected']=selected
            self.stateChanged.emit()

    @Slot()
    def discard(self):
        self.pending=None
        self._notice('已放弃建议，原歌词保持不变。','idle')

    @Slot()
    def apply(self):
        try:
            if not self.pending or self.process:
                raise ValueError('没有可应用的已完成结果')
            pending=self.pending
            request=pending['source']
            if request['scope']!=self.scope:
                raise ValueError('目标歌曲已切换，原结果未应用')
            current=self._source()
            if current['track']['versionId']!=request['track']['versionId'] or current['snapshot']!=request['snapshot']:
                raise ValueError('歌曲或歌词已经修改，旧建议不能覆盖当前内容')
            if pending['kind']=='correct':
                rows=copy.deepcopy(request['rows'])
                selected=[row for row in pending['proposal']['changes'] if row['selected']]
                if not selected:
                    raise ValueError('请选择至少一条纠错建议')
                for change in selected:
                    row=rows[change['index']]
                    if row['text']!=change['before']:
                        raise ValueError('纠错建议与原歌词不一致')
                    if pending['proposal'].get('method')=='reference_text_pinyin':
                        replacement=pending['proposal']['rows'][change['index']]
                        if replacement['text']!=change['after'] or replacement['startMs']!=row['startMs'] or replacement['endMs']!=row['endMs']:
                            raise ValueError('参考纠错改变了实际时间码，未应用')
                        # Independently verify the word clock before accepting the proposal.
                        field='characters' if row.get('characters') else 'words'
                        words=row.get(field,[])
                        revised=replacement.get(field,[])
                        if revised and ([(w['startMs'],w['endMs']) for w in words]!=[(w['startMs'],w['endMs']) for w in revised]
                                        or ''.join(w['text'] for w in revised)!=change['after']):
                            raise ValueError('参考纠错的字词时码与识别原文不一致，未应用')
                        rows[change['index']]=copy.deepcopy(replacement)
                    else:
                        row.update(text=change['after'],timingQuality='sentence')
                        row.pop('words',None)
                        row.pop('characters',None)
                timeline=copy.deepcopy(request['timeline']) if request['timeline'] else None
                if timeline:
                    quality='word' if all(row.get('words') for row in rows) else 'sentence'
                    timeline.update(segments=rows,quality=quality,timingProvenance='reference_corrected_native' if pending['proposal'].get('reference') else 'ai_corrected_sentence',acousticAccuracyVerified=False)
                    if quality=='sentence':
                        for row in rows:
                            row.pop('words',None)
                    if pending['proposal'].get('reference'):
                        timeline['referenceCorrection']=copy.deepcopy(pending['proposal']['reference'])
                    validate_timeline(timeline,current['track']['versionId'])
            else:
                timeline=pending['proposal'].get('timeline')
                if not timeline:
                    raise ValueError('此服务没有返回时间码，请保存文字并手动对齐')
                validate_timeline(timeline,current['track']['versionId'])
                rows=studio_rows(timeline) if self.scope=='studio' else timeline['segments']
            if pending['proposal'].get('reference'):
                _atomic(self.runtime/'reference-audits'/(uuid.uuid4().hex+'.json'),{
                    'versionId':current['track']['versionId'],'sourceSnapshot':current['snapshot'],
                    'reference':pending['proposal']['reference'],'changes':selected,'originalRows':request['rows'],
                    'selectedRows':rows,'phase':'reviewed-selection','originalClockUsed':False})
            if self.scope=='studio':
                self.studio.applyAIResult(rows,current['track']['versionId'],current['snapshot'])
            else:
                track=current['track']
                if not _proof_current(track):
                    raise ValueError('音频文件已经变化，请重新核验')
                self.controller.library.add_timeline(timeline,track)
                self.controller.preparation_timeline(track['versionId'],timeline)
            self.pending=None
            message='已应用原版匹配修改；实际句时码保留，可在制作页撤销。' if pending['proposal'].get('reference') else '已应用所选结果；歌曲句时码保持原样，纠错未重新进行声学对齐。' if pending['kind']=='correct' else '已应用服务返回的真实时间码，歌词已绑定当前歌曲版本。'
            self._notice(message,'applied')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self._notice(exc)

    @Slot()
    def saveTranscript(self):
        text=self.state['previewText']
        if not text:
            return
        path,_=QFileDialog.getSaveFileName(None,'保存识别参考文字','','文本 (*.txt)')
        if path:
            try:
                with Path(path).open('x',encoding='utf-8') as stream:
                    stream.write(text+'\n')
                self._notice('参考文字已保存，尚未生成或修改时间码。','saved')
            except OSError:
                self._notice('保存失败或目标文件已存在，请选择新的文件名')

    @Slot()
    def cancel(self):
        process,self.process=self.process,None
        self.current=None
        if process:
            process.kill()
            process.waitForFinished(500)
            process.deleteLater()
        self._notice('已停止本机AI任务；服务可能已经受理，未自动重新提交。','cancelled')

    def _offline_changed(self):
        if self.scope!='studio' and self._target()!=self._last_target:
            self._target_changed()
        offline=self._offline()
        if offline==self._last_offline:
            return
        self._last_offline=offline
        if offline and self.process and self.current['request']['action'] not in ('plan','match-reference') and not self.current['request'].get('offline'):
            self.cancel()
        elif not self._closed:
            self.stateChanged.emit()

    @Slot()
    def shutdown(self):
        self._closed=True
        if self.process:
            self.cancel()
        self.keys.clear()
