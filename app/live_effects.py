"""Original live glyph transitions, queried only from the source clock.

No wall clock, random generator, retained outgoing frame or external assets.
"""
import math

LIVE_EFFECTS = ('none', 'dissolve', 'ripple', 'rgb-split', 'glitch')
TRANSITION_MS = 300


def transition_at(style, clock):
    """Return (effect, visibility, source step), or None for unchanged ink."""
    if not clock:
        return None
    start, end, position = (clock.get(key) for key in ('startMs', 'endMs', 'positionMs'))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start, end, position)) or end <= start:
        return None
    # A positive lyric delay may query before source zero. Do not show the
    # complete line before its selected entrance, then suddenly hide it at zero.
    if position < start and style.get('liveEntrance') in LIVE_EFFECTS[1:]:
        return style['liveEntrance'], 0., math.floor(position / 33)
    span = min(TRANSITION_MS, (end - start) / 4)
    for field, remaining in (('liveEntrance', position - start), ('liveExit', end - position)):
        effect = style.get(field, 'none')
        if effect in LIVE_EFFECTS[1:] and 0 <= remaining < span:
            return effect, remaining / span, math.floor(position / 33)
    return None


def paint_transition(painter, draw, bounds, transition):
    """Rasterize only the affected line; keep all empty pixels transparent."""
    if transition is None:
        draw(painter)
        return
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    effect, visibility, step = transition
    rect = bounds.toAlignedRect()
    if rect.isEmpty():
        return
    image = QImage(rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    ink = QPainter(image)
    ink.setRenderHints(painter.renderHints())
    ink.translate(-rect.x(), -rect.y())
    try:
        draw(ink)
    finally:
        ink.end()
    alpha = visibility * visibility * (3 - 2 * visibility)
    amount = 1 - visibility
    origin = QPointF(rect.x(), rect.y())
    painter.save()
    painter.setOpacity(painter.opacity() * alpha)
    if effect == 'ripple':
        band = max(1, round(rect.height() / 70))
        for y in range(0, image.height(), band):
            offset = math.sin(y / max(1, rect.height()) * math.pi * 6 + visibility * math.pi * 2)
            offset *= rect.height() * .055 * amount
            source = QRectF(0, y, image.width(), min(band, image.height() - y))
            painter.drawImage(source.translated(origin + QPointF(offset, 0)), image, source)
    elif effect == 'rgb-split':
        for color, direction in (('#FF557A', -1), ('#56E8E3', 1)):
            tint = image.copy()
            mask = QPainter(tint)
            mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            mask.fillRect(tint.rect(), QColor(color))
            mask.end()
            painter.save()
            painter.setOpacity(painter.opacity() * .65 * amount)
            painter.drawImage(origin + QPointF(direction * rect.height() * .065 * amount, 0), tint)
            painter.restore()
        painter.drawImage(origin, image)
    elif effect == 'glitch':
        band = max(2, round(rect.height() / 15))
        for index, y in enumerate(range(0, image.height(), band)):
            # Discrete, reproducible slices; pause never keeps flickering.
            noise = math.sin((index + 1) * 17.13 + step * 9.71)
            offset = noise * rect.height() * .11 * amount if index % 3 else 0
            source = QRectF(0, y, image.width(), min(band, image.height() - y))
            painter.drawImage(source.translated(origin + QPointF(offset, 0)), image, source)
    else:
        painter.drawImage(origin, image)
    painter.restore()
