"""Original live lyric glyph rendering shared by both Qt Quick backends.

QTextLayout supplies shaping, line breaks and fallback fonts. QPainter fills the
actual glyph outlines, so a gradient never paints the empty rectangle around a
caption. There is no paint timer: text/style/geometry changes invalidate once.
"""
from PySide6.QtCore import Property, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient,
                          QPainter, QPainterPath, QPen, QTextLayout, QTextOption,
                          QTransform)
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickPaintedItem
from app.live_effects import paint_transition, transition_at


def _appearance_property(name, value_type, notify, layout=True):
    def get(item):
        return item._values[name]

    def set_value(item, value):
        if value != item._values[name]:
            item._values[name] = value
            if layout:
                item._schedule_layout()
            else:
                item.update()
            item.appearanceChanged.emit()

    return Property(value_type, get, set_value, notify=notify)


class GradientLyricItem(QQuickPaintedItem):
    appearanceChanged = Signal()
    metricsChanged = Signal()
    # Declare notify signals and their properties in the same meta-object.
    # Using a base class Signal descriptor in generated subclass properties
    # makes PySide build invalid notify indices when QML caches the type.
    text = _appearance_property('text', str, appearanceChanged)
    font = _appearance_property('font', QFont, appearanceChanged)
    textColor = _appearance_property('textColor', QColor, appearanceChanged)
    gradientColor = _appearance_property('gradientColor', QColor, appearanceChanged)
    outlineColor = _appearance_property('outlineColor', QColor, appearanceChanged)
    outlineWidth = _appearance_property('outlineWidth', float, appearanceChanged)
    shadowEnabled = _appearance_property('shadowEnabled', bool, appearanceChanged)
    shadowColor = _appearance_property('shadowColor', QColor, appearanceChanged)
    shadowBlur = _appearance_property('shadowBlur', float, appearanceChanged)
    shadowOffsetX = _appearance_property('shadowOffsetX', float, appearanceChanged)
    shadowOffsetY = _appearance_property('shadowOffsetY', float, appearanceChanged)
    textAlignment = _appearance_property('textAlignment', int, appearanceChanged)
    wrap = _appearance_property('wrap', bool, appearanceChanged)
    elideMode = _appearance_property('elideMode', int, appearanceChanged)
    padding = _appearance_property('padding', float, appearanceChanged)
    gradientEnabled = _appearance_property('gradientEnabled', bool, appearanceChanged, False)
    sweepEnabled = _appearance_property('sweepEnabled', bool, appearanceChanged, False)
    sweepCursor = _appearance_property('sweepCursor', 'QVariantList', appearanceChanged, False)
    sweepProgress = _appearance_property('sweepProgress', float, appearanceChanged, False)
    sweepColor = _appearance_property('sweepColor', QColor, appearanceChanged, False)
    effectSettings = _appearance_property('effectSettings', 'QVariantMap', appearanceChanged, False)
    effectClock = _appearance_property('effectClock', 'QVariantMap', appearanceChanged, False)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = dict(text='', font=QFont('PingFang SC', 24),
                            textColor=QColor('#FFFFFF'), gradientColor=QColor('#FFD26B'),
                            outlineColor=QColor('#000000'), outlineWidth=1.,
                            shadowEnabled=False, shadowColor=QColor('#000000'),
                            shadowBlur=6., shadowOffsetX=0., shadowOffsetY=3.,
                            textAlignment=Qt.AlignmentFlag.AlignHCenter.value,
                            wrap=True, elideMode=Qt.TextElideMode.ElideNone.value, padding=20.,
                            gradientEnabled=True, sweepEnabled=False, sweepCursor=[],
                            sweepProgress=0., sweepColor=QColor('#FFE600'), effectSettings={}, effectClock={})
        self._path = QPainterPath()
        self._layout = None
        self._text_height = 0.
        self._lines = 0
        self._ink_rect = None
        self._layout_pending = False
        self.setAntialiasing(True)
        self.widthChanged.connect(self._schedule_layout)

    @Property(float, notify=metricsChanged)
    def textHeight(self):
        return self._text_height

    @Property(int, notify=metricsChanged)
    def lineCount(self):
        return self._lines

    def _schedule_layout(self):
        if not self._layout_pending:
            self._layout_pending = True
            # Coalesce QML writes and wait for geometry bindings to settle.
            # Reading width during its own binding evaluation re-enters QML.
            QTimer.singleShot(0, self, self._rebuild)

    def _rebuild(self):
        self._layout_pending = False
        values = self._values
        width = max(1., self.width() - 2 * values['padding'])
        text = values['text']
        font = values['font']
        if values['elideMode'] != Qt.TextElideMode.ElideNone.value:
            text = QFontMetricsF(font).elidedText(text.replace('\n', ' '),
                                                Qt.TextElideMode(values['elideMode']), width)
        # QTextLayout is a single paragraph layout; U+2028 retains explicit line
        # breaks while also allowing Qt's language-aware wrapping and shaping.
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\u2028')
        layout = QTextLayout(text, font)
        options = QTextOption()
        options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
                            if values['wrap'] else QTextOption.WrapMode.NoWrap)
        options.setAlignment(Qt.Alignment(values['textAlignment']))
        layout.setTextOption(options)
        layout.beginLayout()
        y, count = 0., 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, y))
            y += line.height()
            count += 1
        layout.endLayout()
        path = QPainterPath()
        for run in layout.glyphRuns():
            raw_font = run.rawFont()
            for glyph, position in zip(run.glyphIndexes(), run.positions()):
                glyph_path = raw_font.pathForGlyph(glyph)
                path.addPath(QTransform.fromTranslate(position.x(), position.y()).map(glyph_path))
        self._layout, self._path = layout, path
        self._ink_rect = path.boundingRect()
        if self._text_height != y or self._lines != count:
            self._text_height, self._lines = y, count
            self.metricsChanged.emit()
        self.update()

    def _sweep_clip(self):
        """Wipe shaped, aligned lines in reading order, including wrapped tokens."""
        import math
        values = self._values
        source = values['text']
        cursor = values['sweepCursor']
        if len(cursor) == 3 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in cursor):
            first, last, fraction = cursor
        else:
            first, last, fraction = 0, len(source), values['sweepProgress']
        def utf16(index):
            prefix = source[:max(0,min(len(source),int(index)))].replace('\r\n','\n').replace('\r','\n')
            return len(prefix.encode('utf-16-le')) // 2
        first, last = utf16(first), utf16(last)
        fraction = max(0.,min(1.,fraction))
        pieces=[]
        for index in range(self._layout.lineCount()):
            line=self._layout.lineAt(index)
            start, end=line.textStart(),line.textStart()+line.textLength()
            a=max(start,min(end,first));b=max(start,min(end,last))
            left=line.cursorToX(a)[0];right=line.cursorToX(b)[0]
            pieces.append((line,left,right,abs(right-left)))
        remaining=fraction*sum(piece[3] for piece in pieces)
        clip=QPainterPath()
        for line,left,right,length in pieces:
            rect=line.naturalTextRect()
            edge=left+(right-left)*min(1.,remaining/length) if length else left
            remaining=max(0.,remaining-length)
            if edge>rect.left():
                clip.addRect(QRectF(rect.left(),line.y(),edge-rect.left(),line.height()))
        return clip

    def paint(self, painter):
        if not self._values['text'] or self._layout is None:
            return
        paint_transition(painter, self._paint_ink, QRectF(0, 0, self.width(), self.height()),
                         transition_at(self._values['effectSettings'], self._values['effectClock']))

    def _paint_ink(self, painter):
        values = self._values
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.translate(values['padding'], values['padding'])
        path = self._path
        if values['shadowEnabled']:
            painter.save()
            painter.translate(values['shadowOffsetX'], values['shadowOffsetY'])
            shadow = QColor(values['shadowColor'])
            shadow.setAlphaF(.5 * shadow.alphaF())
            painter.fillPath(path, shadow)
            radius = values['shadowBlur'] * .45
            if radius > 0:
                from math import cos, pi, sin
                shadow.setAlphaF(.12 * QColor(values['shadowColor']).alphaF())
                for index in range(8):
                    shifted = QTransform.fromTranslate(cos(index * pi / 4) * radius,
                                                       sin(index * pi / 4) * radius).map(path)
                    painter.fillPath(shifted, shadow)
            painter.restore()
        if values['outlineWidth'] > 0:
            # A Qt Text outline extends outward by the configured radius.
            pen = QPen(values['outlineColor'], values['outlineWidth'] * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
        rect = self._ink_rect
        gradient = QLinearGradient(0., rect.top(), 0., max(rect.top() + 1., rect.bottom()))
        gradient.setColorAt(0., values['textColor'])
        gradient.setColorAt(1., values['gradientColor'])
        painter.fillPath(path, QBrush(gradient) if values['gradientEnabled'] else QBrush(values['textColor']))
        if values['sweepEnabled']:
            painter.save()
            painter.setClipPath(self._sweep_clip(), Qt.ClipOperation.IntersectClip)
            painter.fillPath(path, values['sweepColor'])
            painter.restore()


_registered = False


def register_lyric_types():
    global _registered
    if not _registered:
        from app.live_karaoke import LiveKaraokeItem
        qmlRegisterType(GradientLyricItem, 'Baofang.Lyric', 1, 0, 'GradientLyricItem')
        qmlRegisterType(LiveKaraokeItem, 'Baofang.Lyric', 1, 0, 'LiveKaraokeItem')
        _registered = True
