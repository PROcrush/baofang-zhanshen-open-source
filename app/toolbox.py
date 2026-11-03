"""Independent local material, video and concert-candidate workflows."""
import copy
import json
import os
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Property, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from app import studio_tools
from app.studio import file_identity, check_identity
from worker.studio_render import find_ffmpeg, probe_media


class ToolboxController(QObject):
    stateChanged = Signal()
    completed = Signal(str, object)

    def __init__(self, root, runtime):
        super().__init__()
        self.runtime = Path(runtime) / 'toolbox'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self._state = {'busy':False, 'status':'选择素材目录、视频或两份歌词开始处理。',
                       'media':[], 'duplicates':[], 'folder':'', 'videoPath':'', 'videoTitle':'',
                       'videoWidth':0, 'videoHeight':0, 'roi':{'x':16,'y':100,'width':200,'height':50},
                       'resultPath':'', 'sourceLyrics':'', 'targetLyrics':'', 'pairingRows':[],
                       'mappingPath':'', 'progress':0, 'error':False}
        self._process = None
        self._source_rows = self._target_rows = []
        self._source_identity = None
        self.completed.connect(self._accept)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.cancel)

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return copy.deepcopy(self._state)

    def _status(self, message, error=False):
        self._state.update(status=message,error=error)
        self.stateChanged.emit()

    def _work(self, key, message, function):
        if self._state['busy']:
            return
        self._state.update(busy=True,error=False,status=message)
        self.stateChanged.emit()
        def run():
            try:
                result = function()
            except Exception as error:
                result = {'error':str(error)}
            try:
                self.completed.emit(key,result)
            except RuntimeError:
                pass
        threading.Thread(target=run,daemon=True).start()

    @Slot(str, object)
    def _accept(self, key, result):
        self._state['busy'] = False
        if 'error' in result:
            self._status(result['error'],True)
            return
        if key == 'media':
            self._state.update(folder=result['root'],media=result['files'],duplicates=[])
            self._status('已读入 '+str(len(result['files']))+' 份本地素材'+('，已到文件上限' if result['truncated'] else ''))
        elif key == 'duplicates':
            self._state['duplicates'] = result['groups']
            self._status('发现 '+str(len(result['groups']))+' 组内容相同的文件，原文件保持原样。')
        elif key == 'video':
            video = next((row for row in result['probe']['streams'] if row.get('codec_type')=='video'),None)
            if not video:
                self._status('所选文件没有视频轨道',True)
                return
            self._source_identity = result['identity']
            width,height = int(video['width']),int(video['height'])
            self._state.update(videoPath=result['identity']['path'],videoTitle=Path(result['identity']['path']).name,
                               videoWidth=width,videoHeight=height,resultPath='',
                               roi={'x':max(1,int(width*.02)), 'y':max(1,int(height*.78)),
                                    'width':max(4,int(width*.96)-2),'height':max(4,int(height*.16))})
            self._status('视频已载入，调整字幕区域后生成 5 秒样片。')
        elif key == 'pairing':
            self._state.update(pairingRows=result['rows'],mappingPath=result['mappingPath'])
            self._status('已生成 '+str(len(result['rows']))+' 段映射建议，请复核演唱版本与画面。')

    @Slot()
    def chooseFolder(self):
        if self._state['busy']: return
        path = QFileDialog.getExistingDirectory(None,'选择本地素材目录')
        if path: self.loadFolder(path)

    @Slot(str)
    def loadFolder(self,path):
        self._work('media','正在读取本地素材目录',lambda: studio_tools.index_media_directory(path))

    @Slot()
    def findDuplicates(self):
        rows = copy.deepcopy(self._state['media'])
        self._work('duplicates','正在检查内容相同的素材',lambda: {'groups':studio_tools.suggest_duplicate_files(rows)})

    @Slot()
    def chooseVideo(self):
        if self._state['busy']: return
        path,_ = QFileDialog.getOpenFileName(None,'选择要处理的视频','','视频 (*.mp4 *.mov *.mkv *.avi)')
        if path: self.loadVideo(path)

    @Slot(str)
    def loadVideo(self,path):
        self._work('video','正在核验视频',lambda: {'identity':file_identity(path),'probe':probe_media(path,include_video=True)})

    @Slot(str,int)
    def setROI(self,key,value):
        if key in self._state['roi'] and value >= 0:
            self._state['roi'][key]=value
            self.stateChanged.emit()

    @Slot()
    def previewRemoval(self):
        if self._state['busy'] or not self._source_identity: return
        try:
            check_identity(self._source_identity)
            job=self.runtime/uuid.uuid4().hex
            job.mkdir()
            output=job/'去字幕样片.mp4'
            args=studio_tools.build_delogo_command(self._state['videoPath'],output,self._state['roi'],
                 ffmpeg_path=find_ffmpeg(),preview_start=0,preview_duration=5,
                 frame_size=(self._state['videoWidth'],self._state['videoHeight']))
        except Exception as error:
            self._status(str(error),True)
            return
        source=copy.deepcopy(self._source_identity)
        process=QProcess(self)
        self._process=process
        self._state.update(busy=True,resultPath='',status='正在生成 5 秒去字幕样片',error=False)
        self.stateChanged.emit()
        errors=bytearray()
        process.readyReadStandardError.connect(lambda:errors.extend(bytes(process.readAllStandardError())))
        def done(code,status):
            self._timer.stop()
            self._state['busy']=False
            self._process=None
            try:
                if code or status != QProcess.ExitStatus.NormalExit or not output.is_file():
                    raise ValueError('去字幕样片未完成：'+errors.decode(errors='replace')[-400:])
                check_identity(source)
                self._state['resultPath']=str(output)
                self._status('样片已生成：固定区域像素修补，可先查看效果。')
            except Exception as error:
                self._status(str(error),True)
            process.deleteLater()
        process.finished.connect(done)
        def failed_start(error):
            if error==QProcess.ProcessError.FailedToStart:
                self._timer.stop()
                self._state['busy']=False
                self._process=None
                self._status('无法启动视频处理',True)
                process.deleteLater()
        process.errorOccurred.connect(failed_start)
        process.start(args[0],args[1:])
        self._timer.start(120000)

    @Slot(str)
    def chooseLyrics(self,side):
        if self._state['busy'] or side not in ('source','target'):return
        path,_=QFileDialog.getOpenFileName(None,'选择'+('演唱会' if side=='source' else '目标歌曲')+'歌词','','字幕 (*.srt *.lrc *.json)')
        if path: self.loadLyrics(side,path)

    @Slot(str,str)
    def loadLyrics(self,side,path):
        try:
            data=studio_tools.load_subtitles(path)
            if side=='source': self._source_rows=data['segments']
            elif side=='target': self._target_rows=data['segments']
            else: return
            self._state[side+'Lyrics']=str(Path(path).resolve())
            self._state['pairingRows']=[]
            self._status('已读取 '+str(len(data['segments']))+' 句歌词')
        except Exception as error:
            self._status(str(error),True)

    @Slot()
    def suggestPairing(self):
        if not self._source_rows or not self._target_rows:return
        source,target=copy.deepcopy(self._source_rows),copy.deepcopy(self._target_rows)
        def work():
            pairing=studio_tools.suggest_concert_pairing(source,target)
            rows=studio_tools.map_concert_segments(pairing)
            job=self.runtime/uuid.uuid4().hex
            job.mkdir()
            path=job/'演唱会段落映射.json'
            path.write_text(json.dumps({'pairing':pairing,'clips':rows},ensure_ascii=False,indent=2))
            return {'rows':rows,'mappingPath':str(path)}
        self._work('pairing','正在匹配两份歌词的段落',work)

    @Slot(int)
    def openResult(self,page):
        path=self._state['resultPath'] if page==1 else self._state['mappingPath'] if page==2 else ''
        if path: QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot()
    def cancel(self):
        if self._process:
            self._process.kill()
            self._process.waitForFinished(1000)
        self._state['busy']=False
        self._status('任务已取消')

    def shutdown(self):
        self._timer.stop()
        if self._process:self.cancel()
