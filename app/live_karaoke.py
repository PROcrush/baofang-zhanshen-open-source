"""Live traditional KTV uses the studio's final-font painter, without a timer."""
from PySide6.QtCore import Property, Signal
from PySide6.QtGui import QPainter
from PySide6.QtQuick import QQuickPaintedItem

from app.karaoke import sweep_cursor
from app.live_effects import paint_transition, transition_at
from worker.studio_render import _paint_lyric
from worker.style_catalog import DEFAULT_SETTINGS, TRADITIONAL_KARAOKE_STYLE


def live_karaoke_settings(style, width, height):
    """Adapt live preferences to the common 480p reference used by the studio."""
    return dict(DEFAULT_SETTINGS, **dict(TRADITIONAL_KARAOKE_STYLE,
        templateId='karaoke', width=width, height=height, fadeMs=0,
        fontFamily=style.get('fontFamily', 'Heiti SC'), fontSize=style.get('fontSize', 48),
        fontBold=style.get('fontBold', True), letterSpacing=style.get('letterSpacing', 1),
        lineSpacing=style.get('lineSpacing', 23) / 23 * 1.25,
        textColor=style.get('baseColor', '#F7F7F2'), highlightColor=style.get('highlightColor', '#FFE600'),
        strokeColor=style.get('outlineColor', '#101016'), strokeWidth=style.get('outlineWidth', 3),
        shadow=style.get('shadowEnabled', True), y=max(.20, min(.80, style.get('verticalPosition', 78) / 100)),
        karaokeMargin=style.get('horizontalMargin', 7) / 100))


class LiveKaraokeItem(QQuickPaintedItem):
    contentChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lyric, self._style = {}, {}
        self.setAntialiasing(True)
        self.widthChanged.connect(self.update)
        self.heightChanged.connect(self.update)

    @Property('QVariantMap', notify=contentChanged)
    def lyric(self):
        return self._lyric

    @lyric.setter
    def lyric(self, value):
        if value != self._lyric:
            self._lyric = value
            self.contentChanged.emit()
            self.update()

    @Property('QVariantMap', notify=contentChanged)
    def settings(self):
        return self._style

    @settings.setter
    def settings(self, value):
        if value != self._style:
            self._style = value
            self.contentChanged.emit()
            self.update()

    @Property(bool, notify=contentChanged)
    def canSweep(self):
        frame = self._lyric.get('karaoke')
        return bool(frame and self._style.get('sweepMode') != 'sentence' and
                    sweep_cursor(frame['current'], frame['positionMs']) is not None)

    def paint(self, painter):
        if self.width() <= 0 or self.height() <= 0 or not self._lyric.get('text'):
            return
        frame = self._lyric.get('karaoke')
        if not frame:
            # Static legacy/example captions have no supplied clocks and never sweep.
            current = {'text': self._lyric['text'], 'startMs': 0, 'endMs': 1}
            following = dict(current, text=self._lyric['nextText']) if self._lyric.get('nextText') else None
            frame = dict(current=current, following=following, positionMs=0, waiting=True, lineIndex=0)
        settings = live_karaoke_settings(self._style, self.width(), self.height())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        def render_line(target, draw, bounds, future):
            row = frame['following'] if future else frame['current']
            start = frame.get('followingAppearanceMs' if future else 'appearanceMs')
            clock = dict(startMs=start, endMs=row['endMs'], positionMs=frame['positionMs']) if start is not None else None
            paint_transition(target, draw, bounds, transition_at(self._style, clock))
        _paint_lyric(painter, frame['current'], frame['following'], settings, frame['positionMs'],
                     True, frame['waiting'], frame['lineIndex'],
                     sweep=self._style.get('sweepMode') != 'sentence' and bool(self._lyric.get('karaoke')),
                     line_renderer=render_line)
