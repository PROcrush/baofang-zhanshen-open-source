"""Qt authoring operations for our own composition and concert timeline."""
import copy
import hashlib
import json
import math
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Slot, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog

from app.studio_tools import load_subtitles, export_subtitles, split_segment, merge_segments
from app.runtime_paths import validate_model_location


def domain():
    from app import composition
    return composition


def finite(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '必须是有限数字')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(label + '超出范围')
    return float(value)


class StudioAuthoring(QObject):
    @Slot()
    @Slot(str)
    def saveVideoAs(self,path=''):
        if self._state['busy']:return
        output=None;created=False;created_identity=None
        try:
            source=Path(self._state.get('resultPath',''))
            if not self._state.get('resultPath') or not source.is_file():raise ValueError('先生成预览或视频，再保存成品')
            suffix=source.suffix
            if not path:path,_=QFileDialog.getSaveFileName(None,'保存成品视频',str(Path.home()/('包房战神视频'+suffix)), '视频 (*'+suffix+')')
            if not path:return
            output=Path(path).expanduser().absolute()
            if output.suffix.lower()!=suffix:raise ValueError('请选择 '+suffix+' 视频文件名')
            if output.exists() or output.is_symlink():raise ValueError('此文件已存在，请换一个文件名保存')
            before=source.stat()
            self._state.update(busy=True,progress=0,status='正在保存成品视频');self._emit()
            import shutil,os
            with source.open('rb') as reader,output.open('xb') as writer:
                created=True;created_identity=os.fstat(writer.fileno())
                shutil.copyfileobj(reader,writer,1024*1024)
            after=source.stat()
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise ValueError('保存期间成片发生变化，请重新生成后保存')
            saved=output.stat()
            if (saved.st_dev,saved.st_ino,saved.st_size)!=(created_identity.st_dev,created_identity.st_ino,before.st_size):
                raise ValueError('保存目标发生变化，请重新保存到新文件')
            self._state.update(busy=False,progress=1,savedVideoPath=str(output),
                savedVideoUrl=QUrl.fromLocalFile(str(output)).toString(),savedVideoCurrent=True,
                status='视频已保存：'+str(output));self._emit()
        except (OSError,ValueError,TypeError) as error:
            if created and output and output.exists():
                stat=output.stat()
                if (stat.st_dev,stat.st_ino)==(created_identity.st_dev,created_identity.st_ino):output.unlink()
            self._failure('保存视频失败：'+str(error))

    @Slot()
    def openExampleProject(self):
        if self._state['busy']:return
        try:
            from app.studio_examples import create_example
            self.openProject(create_example(self.root,self.runtime))
        except (OSError,ValueError,TypeError) as error:self._failure('示例打开失败：'+str(error))

    def _init_authoring(self):
        from worker.style_catalog import LAYOUTS, TRANSITIONS, TEXT_LAYOUTS
        self._state.update(composition=domain().empty_composition(), materialSources=[],
            transitions=copy.deepcopy(TRANSITIONS), layouts=copy.deepcopy(LAYOUTS), textLayouts=copy.deepcopy(TEXT_LAYOUTS),
            outputFormat='mp4', waveform=[], beatsMs=[], analysisStatus='尚未分析歌曲波形',
            concertRows=[], concertSourceId='', concertLyricsPath='', concertStatus='导入演唱会视频和对应歌词',
            concertCoverage={}, concertReviewed=False, concertGapSourceId='', concertAnalysis={}, advancedBoundary='高级制作使用包房战神自己的分镜与渲染引擎')
        self._state.update(asrModelPath=str(self.root/'assets/models/tiny.pt'),asrStatus='使用本机独立Whisper模型识别，不上传歌曲')
        self._concert_draft = None
        self._last_lyric_digest = self._lyric_digest()

    def _lyric_digest(self):
        return hashlib.sha256(json.dumps(self._state['segments'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def _check_lyric_invalidation(self):
        current=self._lyric_digest()
        if current == self._last_lyric_digest:return
        self._last_lyric_digest=current
        self._concert_draft=None
        value=self._state['composition']
        if value.get('concert',{}).get('coverage'):
            for row in value['clips']:
                if row.get('purpose'):row['reviewed']=False
            self._state['composition']=domain().refresh_concert_coverage(value,self._state['durationMs'])
            self._state['concertStatus']='主歌曲歌词已修改，已有编排需要重新复核'
        self._sync_authoring()

    def _sync_authoring(self):
        value = self._state['composition']
        self._state['materialSources'] = [{'id': key, 'title':Path(row['identity']['path']).name,
            'path':row['identity']['path'], 'url':QUrl.fromLocalFile(row['identity']['path']).toString(), 'kind':row['kind'], 'durationMs':row['durationMs'],
            'width':row['width'], 'height':row['height'], 'hasAudio':row.get('hasAudio',False)} for key,row in value['sources'].items()]
        concert = value.get('concert', {})
        self._state['concertSourceId'] = concert.get('sourceId', '')
        self._state['concertLyricsPath'] = concert.get('lyricsPath', '')
        self._state['concertGapSourceId'] = concert.get('gapSourceId', '')
        shown = self._concert_draft if self._concert_draft is not None else value
        self._state['concertCoverage'] = copy.deepcopy(shown.get('concert',{}).get('coverage', {}))
        self._state['concertRows'] = copy.deepcopy([row for row in shown['clips'] if row.get('purpose')])
        self._state['concertReviewed'] = bool(self._state['concertRows']) and all(row.get('reviewed',False) for row in self._state['concertRows'])

    def _edit_composition(self, operation, message):
        if self._state['busy']: return
        try:
            from app.studio import check_identity
            result = operation(copy.deepcopy(self._state['composition']))
            result = domain().validate_composition(result, self._state['durationMs'])
            if result != self._state['composition']:
                draft=None
                if self._concert_draft is not None and all(result[key]==self._state['composition'][key] for key in ('clips','concert')):
                    draft=copy.deepcopy(self._concert_draft)
                    for key in ('sources','texts','audioClips','markers','fps'):draft[key]=copy.deepcopy(result[key])
                    draft=domain().validate_composition(draft,self._state['durationMs'])
                self._remember()
                self._state['composition'] = result
                self._concert_draft = draft
                self._sync_authoring()
                self._state['status'] = message
                self._emit()
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._failure(str(error))

    def _load_material(self, path, concert=False):
        if self._state['busy']: return
        try:
            from app.studio import file_identity, check_identity
            identity = file_identity(path)
            suffix = Path(identity['path']).suffix.lower()
            image = suffix in ('.png','.jpg','.jpeg','.webp')
            if image:
                picture = QImage(identity['path'])
                if picture.isNull(): raise ValueError('图片不能解码')
                data = {'format':{'duration':0},'streams':[{'codec_type':'image','width':picture.width(),'height':picture.height()}]}
            else: data = None
            if not self._start_job('正在读取本地素材与源文件身份'): return
            def loaded(probe):
                check_identity(identity)
                video = next((row for row in probe['streams'] if row.get('codec_type') in ('video','image')),None)
                kind = 'image' if image else 'video' if video else 'audio'
                if concert and kind != 'video': raise ValueError('演唱会来源必须包含视频轨道')
                duration = 0 if image else finite(float(probe['format']['duration'])*1000,'素材时长',.01)
                sources = self._state['composition']['sources']
                source_id = next((key for key,row in sources.items() if row['identity']==identity), uuid.uuid4().hex)
                value = copy.deepcopy(self._state['composition'])
                value['sources'][source_id] = {'identity':identity,'kind':kind,'durationMs':duration,
                    'width':int(video.get('width',0)) if video else 0, 'height':int(video.get('height',0)) if video else 0,
                    'hasAudio':any(row.get('codec_type')=='audio' for row in probe['streams'])}
                if concert:
                    value['concert'] = {'sourceId':source_id,'sourceSegments':[], 'lyricsPath':'', 'gapSourceId':'', 'reviewed':False}
                    for row in value['clips']:
                        for key in ('purpose','reviewed','reason'):row.pop(key,None)
                value = domain().validate_composition(value,self._state['durationMs'])
                draft=None if concert else copy.deepcopy(self._concert_draft)
                if draft is not None:
                    draft['sources']=copy.deepcopy(value['sources'])
                    draft=domain().validate_composition(draft,self._state['durationMs'])
                self._remember()
                self._state.update(composition=value,busy=False,progress=1,status='素材已导入，可加入分镜或附加音轨')
                self._concert_draft = draft
                self._sync_authoring()
                if concert:
                    self._state.update(concertAnalysis={},concertStatus='视频已导入，请导入来源视频对应的歌词时码')
                self._emit()
            if data: loaded(data)
            else: self._probe(identity['path'], loaded, include_video=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._failure('素材导入失败：'+str(error))

    @Slot()
    @Slot(str)
    def chooseMaterial(self,path=''):
        if self._state['busy']: return
        if not path:
            path,_=QFileDialog.getOpenFileName(None,'导入制作素材','','素材 (*.mp4 *.mov *.mkv *.avi *.png *.jpg *.jpeg *.webp *.wav *.mp3 *.m4a *.flac);;所有文件 (*)')
        if path: self._load_material(path)

    @Slot()
    @Slot(str)
    def chooseConcertVideo(self,path=''):
        if self._state['busy']: return
        if not path: path,_=QFileDialog.getOpenFileName(None,'导入演唱会视频','','视频 (*.mp4 *.mov *.mkv *.avi)')
        if path: self._load_material(path,True)

    @Slot(str)
    def addClip(self,source_id):
        def operation(value):
            if self._state['durationMs']<=0: raise ValueError('先导入主歌曲，建立制作时钟')
            source=value['sources'].get(source_id)
            if not source or source['kind'] not in ('image','video'): raise ValueError('请选择图片或视频素材')
            start=max((row['startMs']+row['durationMs'] for row in value['clips']),default=0)
            if start>=self._state['durationMs']: raise ValueError('画面时间轴已填满，可先裁剪或删除分镜')
            duration=min(4000,self._state['durationMs']-start)
            if source['kind']=='video': duration=min(duration,source['durationMs'])
            value['clips'].append(domain().make_clip(source_id,source['kind'],start,duration))
            return value
        self._edit_composition(operation,'已加入画面分镜')

    @Slot()
    def addColorClip(self):
        def operation(value):
            start=max((row['startMs']+row['durationMs'] for row in value['clips']),default=0)
            length=min(4000,self._state['durationMs']-start)
            if length<=0: raise ValueError('请先导入歌曲或腾出时间轴位置')
            value['clips'].append(domain().make_clip('','color',start,length,color=self._state['settings']['backgroundColor']))
            return value
        self._edit_composition(operation,'已加入纯色分镜')

    @Slot(str,str,'QVariant')
    def editClip(self,identifier,key,value):
        if key=='transition': return self._failure('请使用转场设置')
        self._edit_composition(lambda content:domain().edit_clip(content,identifier,{key:value},self._state['durationMs']),'分镜已更新')

    @Slot(str,float,float,float)
    def editClipRange(self,identifier,start,duration,source_in):
        self._edit_composition(lambda content:domain().edit_clip(content,identifier,
            {'startMs':start,'durationMs':duration,'sourceInMs':source_in},self._state['durationMs']),
            '分镜位置和裁剪已更新')

    @Slot(str,float)
    def splitClip(self,identifier,position):
        self._edit_composition(lambda content:domain().split_clip(content,identifier,position,self._state['durationMs']),'分镜已拆分，源位置与速度保持连续')

    @Slot(str)
    def removeClip(self,identifier):
        self._edit_composition(lambda content:domain().remove_clip(content,identifier,self._state['durationMs']),'分镜已移除')

    @Slot(str,str,float)
    def setTransition(self,identifier,transition_id,duration):
        if transition_id not in {row['id'] for row in self._state['transitions']}: return self._failure('转场不存在')
        self._edit_composition(lambda content:domain().edit_clip(content,identifier,{'transition':{'id':transition_id,'durationMs':duration}},self._state['durationMs']),'相邻分镜转场已更新，时长已对齐到帧')

    @Slot()
    def autoStoryboard(self):
        def operation(value):
            if not self._state['segments']: raise ValueError('先导入歌词再按句生成分镜')
            if any(row.get('locked') for row in value['clips']): raise ValueError('请先解除现有分镜锁定再自动编排')
            return domain().generate_storyboard(value,self._state['segments'],self._state['durationMs'])
        self._edit_composition(operation,'已按歌词生成完整分镜，可逐段修改与设置转场')

    @Slot()
    def addText(self):
        def operation(value):
            start=min(self._state['positionMs'],max(0,self._state['durationMs']-250))
            length=min(3000,self._state['durationMs']-start)
            if length<=0: raise ValueError('请先导入歌曲')
            value['texts'].append(domain().make_text('输入标题',start,length))
            return value
        self._edit_composition(operation,'已加入独立文字，可在属性中修改')

    @Slot(str,str,'QVariant')
    def editText(self,identifier,key,value):
        self._edit_composition(lambda content:domain().edit_text(content,identifier,{key:value},self._state['durationMs']),'独立文字已更新')

    @Slot(str)
    def removeText(self,identifier):
        self._edit_composition(lambda content:domain().remove_text(content,identifier,self._state['durationMs']),'独立文字已移除')

    @Slot(str)
    def addAudioClip(self,source_id):
        def operation(value):
            source=value['sources'].get(source_id)
            if not source or source['kind'] not in ('audio','video'): raise ValueError('请选择含声音的素材')
            if source.get('hasAudio') is False:raise ValueError('该视频没有声音轨道，不能加入附加音轨')
            start=self._state['positionMs']; length=min(3000,source['durationMs'],self._state['durationMs']-start)
            if length<=0: raise ValueError('没有可加入附加声音的时长')
            value['audioClips'].append({'id':uuid.uuid4().hex,'sourceId':source_id,'startMs':start,'durationMs':length,'sourceInMs':0,'rate':1,'gain':1,'locked':False})
            return value
        self._edit_composition(operation,'附加音轨已加入，导出时与主歌曲混合')

    @Slot(str,str,'QVariant')
    def editAudioClip(self,identifier,key,value):
        self._edit_composition(lambda content:domain().edit_audio_clip(content,identifier,{key:value},self._state['durationMs']),'附加音轨已更新')

    @Slot(str)
    def removeAudioClip(self,identifier):
        self._edit_composition(lambda content:domain().remove_audio_clip(content,identifier,self._state['durationMs']),'附加音轨已移除')

    @Slot(int,float,float)
    def updateSegmentTiming(self,index,start,end):
        if self._state['busy']: return
        try:
            from app.studio import validate_segments
            rows=copy.deepcopy(self._state['segments'])
            if not 0<=index<len(rows): raise ValueError('歌词句不存在')
            start=finite(start,'开始时码',0);end=finite(end,'结束时码',0)
            row=rows[index];delta=start-row['startMs']
            if row.get('characters'):
                if abs((end-start)-(row['endMs']-row['startMs']))<.001:
                    for char in row['characters']:char['startMs']+=delta;char['endMs']+=delta
                else:row.pop('characters',None);row['timingQuality']='sentence'
            row.update(startMs=start,endMs=end)
            rows=validate_segments(rows,self._state['durationMs'])
            self._remember();self._state['segments']=rows;self._state['status']='歌词时码已更新';self._emit()
        except (ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(int,float,int)
    def splitSegment(self,index,position,text_index):
        if self._state['busy']:return
        try:
            from app.studio import to_recognition,from_recognition
            rows=split_segment(to_recognition(self._state['segments'])['segments'],index,position/1000,text_index)
            rows=from_recognition({'segments':rows},self._state['durationMs'])
            self._remember();self._state['segments']=rows;self._state['status']='歌词句已拆分';self._emit()
        except (ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(int)
    def mergeSegment(self,index):
        if self._state['busy']:return
        try:
            from app.studio import to_recognition,from_recognition
            rows=merge_segments(to_recognition(self._state['segments'])['segments'],[index,index+1])
            rows=from_recognition({'segments':rows},self._state['durationMs'])
            self._remember();self._state['segments']=rows;self._state['status']='相邻歌词句已合并';self._emit()
        except (ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(int,int,float,float)
    def updateCharacterTiming(self,index,char_index,start,end):
        if self._state['busy']:return
        try:
            from app.studio import validate_segments
            rows=copy.deepcopy(self._state['segments'])
            if not 0<=index<len(rows) or not 0<=char_index<len(rows[index].get('characters',[])):
                raise ValueError('该句没有可编辑的真实逐字时码，请导入增强LRC或识别时码')
            rows[index]['characters'][char_index].update(startMs=finite(start,'逐字起点',0),endMs=finite(end,'逐字终点',0))
            rows=validate_segments(rows,self._state['durationMs'])
            self._remember();self._state['segments']=rows;self._state['status']='逐字时码已更新';self._emit()
        except (ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(str)
    @Slot(str,str)
    def exportSubtitles(self,format,path=''):
        if self._state['busy']:return
        try:
            from app.studio import to_recognition
            if format not in ('srt','lrc'):raise ValueError('请选择SRT或LRC')
            if not path:path,_=QFileDialog.getSaveFileName(None,'导出歌词',str(self.runtime/('歌词.'+format)),format.upper()+' (*.'+format+')')
            if not path:return
            export_subtitles(to_recognition(self._state['segments'])['segments'],format,path)
            self._state['status']='歌词已导出：'+str(path);self._emit()
        except (OSError,ValueError,TypeError) as error:self._failure(str(error))

    @Slot()
    @Slot(str)
    def chooseConcertSubtitles(self,path=''):
        if self._state['busy']:return
        if not path:path,_=QFileDialog.getOpenFileName(None,'演唱会视频对应的歌词','','字幕 (*.srt *.lrc *.json)')
        if not path:return
        try:
            from app.studio import file_identity,check_identity
            identity=file_identity(path)
            concert=self._state['composition']['concert']
            source=self._state['composition']['sources'].get(concert.get('sourceId'))
            if not source:raise ValueError('请先导入演唱会视频')
            data=load_subtitles(path,duration=source['durationMs']/1000)
            check_identity(identity)
            def operation(value):
                value['concert']={'sourceId':concert['sourceId'],'sourceSegments':data['segments'],
                    'lyricsPath':identity['path'],'lyricsIdentity':identity,'gapSourceId':concert.get('gapSourceId',''),'reviewed':False}
                for row in value['clips']:
                    for key in ('purpose','reviewed','reason'):row.pop(key,None)
                return value
            self._edit_composition(operation,'演唱会来源歌词已导入')
            self._state['concertStatus']='已读取 '+str(len(data['segments']))+' 句来源歌词，可以生成候选';self._emit()
        except (OSError,ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(str)
    def setConcertGapSource(self,source_id):
        def operation(value):
            if source_id and (source_id not in value['sources'] or value['sources'][source_id]['kind'] not in ('video','image')):
                raise ValueError('补镜须选择图片或视频素材')
            value['concert']['gapSourceId']=source_id
            return value
        self._edit_composition(operation,'补镜素材已设置，请重新生成候选')

    @Slot()
    def generateConcertPlan(self):
        if self._state['busy']:return
        try:
            from app.studio import to_recognition,check_identity
            value=copy.deepcopy(self._state['composition']);concert=value['concert']
            if concert.get('lyricsIdentity'):check_identity(concert['lyricsIdentity'])
            source_id=concert.get('sourceId','')
            if not source_id or not concert.get('sourceSegments') or not self._state['segments']:
                raise ValueError('需要演唱会视频、来源歌词与主歌曲歌词')
            draft=domain().build_concert_composition(value,source_id,concert['sourceSegments'],
                to_recognition(self._state['segments'])['segments'],self._state['durationMs'],gap_source_id=concert.get('gapSourceId',''))
            self._remember();self._concert_draft=draft
            self._state['concertRows']=copy.deepcopy(self._concert_draft['clips'])
            self._state['concertCoverage']=copy.deepcopy(self._concert_draft['concert']['coverage'])
            self._state['concertReviewed']=False
            self._state['concertStatus']='已生成完整候选编排，请逐段核对画面与演唱位置后确认应用'
            self._state['status']=self._state['concertStatus'];self._emit()
        except (OSError,ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(int,str,'QVariant')
    def reviewConcertRow(self,index,key,value):
        if self._state['busy'] or self._concert_draft is None:return
        try:
            if not 0<=index<len(self._concert_draft['clips']):raise ValueError('候选不存在')
            identifier=self._concert_draft['clips'][index]['id']
            changes={key:value}
            if key=='sourceId':
                row=self._concert_draft['clips'][index]
                if value==row['sourceId']:return
                source=self._concert_draft['sources'].get(value) if value else None
                if value and (not source or source['kind'] not in ('image','video')):raise ValueError('补镜须选择图片或视频')
                kind=source['kind'] if source else 'color'
                rate=min(1,source['durationMs']/row['durationMs']) if kind=='video' else 1
                if rate<.25:raise ValueError('视频不足以覆盖此段，请缩短片段或另选补镜')
                changes.update(kind=kind,sourceInMs=0,rate=rate,purpose='cutaway',
                    reason='手动更换补镜，需核对画面与衔接')
            draft=domain().edit_clip(self._concert_draft,identifier,changes,self._state['durationMs'])
            self._remember();self._concert_draft=draft
            self._state['concertRows']=copy.deepcopy(self._concert_draft['clips'])
            self._state['concertCoverage']=copy.deepcopy(self._concert_draft['concert']['coverage'])
            self._state['concertReviewed']=all(row.get('reviewed',False) for row in self._state['concertRows'])
            self._state['concertStatus']='候选已修改，请确认修改后的画面与时间范围';self._emit()
        except (ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot(bool)
    def setConcertReviewed(self,reviewed):
        if self._state['busy'] or self._concert_draft is None:return
        value=copy.deepcopy(self._concert_draft)
        for row in value['clips']:row['reviewed']=reviewed
        value=domain().refresh_concert_coverage(value,self._state['durationMs'])
        if value!=self._concert_draft:self._remember()
        self._concert_draft=value;self._state['concertRows']=copy.deepcopy(value['clips'])
        self._state['concertCoverage']=copy.deepcopy(value['concert']['coverage'])
        self._state['concertReviewed']=reviewed;self._emit()

    @Slot()
    def applyConcertPlan(self):
        if self._state['busy']:return
        if self._concert_draft is None or not self._state['concertReviewed']:
            self._failure('请先复核候选并确认；未确认的演唱会编排不会替换当前分镜');return
        if not self._concert_draft['concert'].get('coverage',{}).get('fullCoverage'):
            self._failure('候选时间轴存在空档，请补齐或恢复片段范围后再应用完整编排');return
        if any(row.get('locked') for row in self._state['composition']['clips']):
            self._failure('当前分镜存在锁定，请先解除锁定再应用演唱会编排');return
        draft=copy.deepcopy(self._concert_draft)
        try:
            from app.studio import check_identity
            domain().validate_composition(draft,self._state['durationMs'],identity_validator=check_identity)
            if draft['concert'].get('lyricsIdentity'):check_identity(draft['concert']['lyricsIdentity'])
        except (OSError,ValueError,TypeError) as error:
            self._failure('演唱会源发生变化：'+str(error));return
        self._edit_composition(lambda _:draft,'演唱会编排已应用，可继续裁剪、变速与添加转场')
        self._state['concertStatus']='编排已应用；歌词与画面合成导出使用主歌曲声音';self._emit()

    @Slot()
    def analyzeConcertVideo(self):
        if self._state['busy']:return
        try:
            from app.studio import check_identity
            source=copy.deepcopy(self._state['composition']['sources'].get(self._state['concertSourceId']))
            if not source:raise ValueError('先导入演唱会视频')
            check_identity(source['identity'])
            if not self._start_job('正在分析演唱会真实画面与静止段落'):return
            request=self._write_json('concert-inspect.json',{'path':source['identity']['path'],'durationMs':source['durationMs']})
            def done(output):
                check_identity(source['identity']);data=json.loads(output)
                self._state.update(busy=False,progress=1,concertAnalysis=data,
                    status='画面检查完成：'+str(len(data['stationaryIntervals']))+' 段静止候选，'+str(len(data['sceneCutsMs']))+' 个镜头变化')
                self._emit()
            import sys
            self._launch(sys.executable,[self.root/'worker/studio_video.py','--request',request],done,timeout_ms=1800000)
        except (OSError,ValueError,TypeError,KeyError) as error:self._failure(str(error))

    @Slot()
    def analyzeAudio(self):
        if self._state['busy']:return
        try:
            from app.studio import check_identity
            identity=copy.deepcopy(self._sources['audio'])
            if not identity:raise ValueError('请先导入主歌曲')
            check_identity(identity)
            if not self._start_job('正在读取歌曲波形与节拍起点'):return
            request=self._write_json('audio-analysis.json',{'path':identity['path'],'durationMs':self._state['durationMs'],'bpm':self._state['settings']['bpm']})
            import sys
            def done(output):
                data=json.loads(output);check_identity(identity)
                self._state.update(waveform=data['waveform'],beatsMs=data['beatsMs'],analysisStatus=data['notice'],
                    busy=False,progress=1,status='波形与节拍网格已生成，可调整BPM与起点标记')
                self._emit()
            self._launch(sys.executable,[self.root/'worker/studio_audio.py','--request',request],done,timeout_ms=1800000)
        except (OSError,ValueError,TypeError) as error:self._failure(str(error))

    @Slot(float,str)
    def addMarker(self,position,label):
        self._edit_composition(lambda value:domain().add_marker(value,position,label,self._state['durationMs']),'时间轴标记已添加')

    @Slot(str)
    def removeMarker(self,identifier):
        self._edit_composition(lambda value:domain().remove_marker(value,identifier,self._state['durationMs']),'标记已移除')

    @Slot(str)
    def setOutputFormat(self,format):
        if self._state['busy']:return
        if format not in ('mp4','mov-alpha'):return self._failure('请选择MP4或透明MOV')
        if format != self._state['outputFormat']:
            self._remember();self._state['outputFormat']=format
            self._state['status']='导出格式为 '+('H.264 MP4，含主歌曲与附加音轨' if format=='mp4' else 'ProRes4444 MOV，透明歌词与文字，不带声音')
            self._emit()

    @Slot(str)
    @Slot(str,str)
    def exportTextPNG(self,identifier,path=''):
        if self._state['busy']:return
        try:
            text=next((row for row in self._state['composition']['texts'] if row['id']==identifier),None)
            if not text:raise ValueError('请选择独立文字段')
            if not path:path,_=QFileDialog.getSaveFileName(None,'导出透明文字PNG',str(self.runtime/'文字.png'),'PNG (*.png)')
            if not path:return
            output=Path(path).expanduser().absolute()
            if output.exists() or output.is_symlink():raise ValueError('PNG请另存为新文件，不覆盖已有文件')
            from worker.studio_render import draw_frame
            content=domain().empty_composition();content['texts']=[copy.deepcopy(text)]
            frame=draw_frame([],self._state['settings'],text['startMs']+text['durationMs']/2,composition=content,transparent=True)
            import io
            from PySide6.QtCore import QBuffer,QIODevice
            buffer=QBuffer();buffer.open(QIODevice.WriteOnly)
            if not frame.save(buffer,'PNG'):raise ValueError('文字PNG编码失败')
            with output.open('xb') as file:file.write(bytes(buffer.data()))
            self._state['status']='透明文字PNG已导出：'+str(output);self._emit()
        except (OSError,ValueError,TypeError) as error:self._failure(str(error))

    @Slot()
    @Slot(str)
    def chooseAsrModel(self,path=''):
        if self._state['busy']:return
        if not path:path,_=QFileDialog.getOpenFileName(None,'选择本地Whisper模型','','Whisper模型 (*.pt)')
        if not path:return
        try:
            source=Path(path).resolve(strict=True)
            if not source.is_file() or source.suffix!='.pt' or any(part.endswith('.app') for part in source.parts):
                raise ValueError('请选择独立的Whisper模型文件，不使用应用包里的资源')
            self._state['asrModelPath']=str(source)
            self._state['asrStatus']='本地模型已选择，识别全程在本机进行';self._emit()
        except (OSError,ValueError) as error:self._failure(str(error))

    @Slot()
    def recognizeLocal(self):
        if self._state['busy']:return
        try:
            from app.studio import file_identity,check_identity,from_recognition
            audio=copy.deepcopy(self._sources['audio'])
            if not audio:raise ValueError('请先导入主歌曲')
            check_identity(audio)
            model=file_identity(self._state['asrModelPath'])
            validate_model_location(model['path'], self.root)
            if not self._start_job('本地Whisper正在识别，歌曲不会上传'):return
            output=self._job_dir/'local-recognition.json'
            request=self._write_json('local-asr-request.json',{'audioPath':audio['path'],'durationMs':self._state['durationMs'],
                'modelPath':model['path'],'output':str(output)})
            def done(events):
                check_identity(audio);check_identity(model)
                data=json.loads(output.read_text())
                rows=from_recognition(data,self._state['durationMs'])
                if not rows:raise ValueError('本地模型未识别到可用歌词，已有字幕保持原样')
                self._remember();self._state.update(segments=rows,busy=False,progress=1,
                    status='本地识别完成，共 '+str(len(rows))+' 句；请核对歌唱、伴奏与时码',
                    asrStatus='识别结果来自本机Whisper，模型与音频身份已核验')
                self._emit()
            import sys
            self._launch(sys.executable,[self.root/'worker/studio_transcribe.py','--request',request],done,timeout_ms=7200000)
        except (OSError,ValueError,TypeError,KeyError) as error:self._failure('本地识别未开始：'+str(error))
