"""Create an original, portable-in-workspace example through our v3 schema."""
import array
import json
import math
import uuid
import wave
from pathlib import Path


def create_example(root,runtime):
    from PySide6.QtGui import QColor,QImage,QLinearGradient,QPainter
    from app.composition import empty_composition
    from app.studio import DEFAULT_SETTINGS,ENGINE_ID,PROJECT_KIND,PROJECT_VERSION,file_identity
    folder=Path(runtime)/'studio-examples'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    duration,rate=12,16000
    audio=folder/'示例音乐.wav'
    frequencies=[220,261.63,329.63,293.66]
    samples=array.array('h')
    for index in range(duration*rate):
        seconds=index/rate;note=frequencies[int(seconds/3)%4]
        envelope=min(1,seconds/0.2,(duration-seconds)/0.4)*(.65+.35*math.cos(seconds*math.pi))
        samples.append(round(envelope*(1800*math.sin(2*math.pi*note*seconds)+600*math.sin(2*math.pi*note*2*seconds))))
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate);stream.writeframes(samples.tobytes())
    background=folder/'示例背景.png'
    frame=QImage(854,480,QImage.Format_RGBA8888)
    painter=QPainter(frame);gradient=QLinearGradient(0,0,854,480)
    gradient.setColorAt(0,QColor('#352d4a'));gradient.setColorAt(1,QColor('#172c34'))
    painter.fillRect(frame.rect(),gradient);painter.end()
    if not frame.save(str(background)):raise ValueError('示例背景生成失败')
    words=['灯光亮起，今晚就从这里开始','让每一句歌词，都有自己的画面','把喜欢的声音，留在这个夜晚','下一首歌，继续属于我们']
    segments=[{'startMs':index*3000+250,'endMs':index*3000+2750,'text':text,'timingQuality':'sentence'} for index,text in enumerate(words)]
    project=folder/'从这里开始.bfstudio.json'
    payload={'kind':PROJECT_KIND,'version':PROJECT_VERSION,'engineId':ENGINE_ID,'projectId':str(uuid.uuid4()),
        'durationMs':duration*1000,'segments':segments,'settings':dict(DEFAULT_SETTINGS),
        'sources':{'audio':file_identity(audio),'background':file_identity(background),'reference':None},
        'composition':empty_composition(),'outputFormat':'mp4','concertDraft':None}
    project.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    return str(project)
