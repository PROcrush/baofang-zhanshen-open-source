"""Render the complete template gallery using our preview/export engine."""
import os
import json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QImage,QPainter,QColor,QFont,QLinearGradient
from worker.studio_render import _qt,draw_frame
from worker.style_catalog import DEFAULT_SETTINGS,TEMPLATES,template_settings

ROWS=[{'startMs':0,'endMs':5000,'text':'把今晚唱成回忆','characters':[
    {'text':'把今晚','startMs':0,'endMs':2500},{'text':'唱成回忆','startMs':2500,'endMs':5000}]},
    {'startMs':5500,'endMs':8000,'text':'让每一句都有画面'}]


def main():
    application=_qt()
    target=Path(__file__).resolve().parents[1]/'runtime/templates-20261015'
    target.mkdir(parents=True,exist_ok=True)
    sheet=QImage(1600,4*270,QImage.Format.Format_RGBA8888);sheet.fill(QColor('#17171B'))
    painter=QPainter(sheet)
    for index,template in enumerate(TEMPLATES):
        settings=template_settings(DEFAULT_SETTINGS,template['id']);settings.update(width=854,height=480)
        # Neutral editorial backdrop, generated locally rather than copied from either app.
        background=QImage(854,480,QImage.Format.Format_RGBA8888)
        scene=QPainter(background)
        gradient=QLinearGradient(0,0,854,480)
        gradient.setColorAt(0,QColor('#37433F'));gradient.setColorAt(.55,QColor('#202B2D'));gradient.setColorAt(1,QColor('#101820'))
        scene.fillRect(QRectF(0,0,854,480),gradient);scene.end()
        frame=draw_frame(ROWS,settings,1800,background)
        assert frame.save(str(target/(template['id']+'.png')))
        x,y=(index%4)*400,(index//4)*270
        painter.drawImage(QRectF(x+8,y+8,384,216),frame)
        painter.setPen(QColor('#F0EFF4'));painter.setFont(QFont('PingFang SC',13))
        painter.drawText(QRectF(x+12,y+225,376,32),Qt.AlignmentFlag.AlignVCenter,template['label'])
    painter.end();assert sheet.save(str(target/'template-gallery.png'))
    stress=[]
    for template in TEMPLATES:
        for font_size in (template_settings(DEFAULT_SETTINGS,template['id'])['fontSize'],150):
            settings=template_settings(DEFAULT_SETTINGS,template['id'])
            settings.update(width=854,height=480,fontSize=font_size)
            rows=[{'startMs':0,'endMs':5000,'text':'想用支彩色铅笔画一片森林，愿每个长句都完整显示。Long lyrics stay readable 😀 '*3},
                  {'startMs':5500,'endMs':8000,'text':'下一句也要完整保留'}]
            frame=draw_frame(rows,settings,1800,transparent=True)
            alpha=frame.constBits().tobytes()[3::4]
            visible=[index for index,value in enumerate(alpha) if value]
            assert visible,template['id']+' did not paint the long lyric'
            box=(min(index%854 for index in visible),min(visible)//854,
                 max(index%854 for index in visible),max(visible)//854)
            assert box[0]>=8 and box[1]>=8 and box[2]<846 and box[3]<472,(template['id'],font_size,box)
            stress.append({'template':template['id'],'fontSize':font_size,'bounds':box})
    (target/'drawing-checks.json').write_text(json.dumps({'passed':True,'longLyricChecks':stress},ensure_ascii=False,indent=2))
    print(target/'template-gallery.png')


if __name__=='__main__':main()
