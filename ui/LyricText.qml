import QtQuick
import Baofang.Lyric 1.0

// Shared text rendering uses Qt text on the software and accelerated backends.
Item {
    id: label
    property alias text: content.text
    property alias font: content.font
    property alias wrapMode: content.wrapMode
    property alias horizontalAlignment: content.horizontalAlignment
    property alias elide: content.elide
    property color textColor: "#FFFFFF"
    property bool gradientEnabled: false
    property color gradientColor: "#FFD26B"
    property color outlineColor: "#000000"
    property real outlineWidth: 1
    property bool shadowEnabled: false
    property color shadowColor: "#000000"
    property real shadowBlur: 6
    property real shadowOffsetX: 0
    property real shadowOffsetY: 3
    property bool sweepEnabled: false
    property var sweepCursor: []
    property real sweepProgress: 0
    property color sweepColor: "#FFE600"
    property var effectSettings: ({})
    property var effectClock: ({})
    readonly property bool paintedMode: gradientEnabled || sweepEnabled || (effectSettings.liveEntrance || "none") !== "none" || (effectSettings.liveExit || "none") !== "none"
    readonly property int lineCount: paintedMode ? (gradientGlyphs ? gradientGlyphs.lineCount : 0) : (content ? content.lineCount : 0)
    implicitWidth: content ? content.implicitWidth : 0
    implicitHeight: paintedMode ? (gradientGlyphs ? gradientGlyphs.textHeight : 0) : (content ? content.implicitHeight : 0)
    function updateGradientWidth() {
        if (gradientGlyphs) gradientGlyphs.width = label.width + gradientGlyphs.effectPadding * 2
    }
    onWidthChanged: updateGradientWidth()
    Component.onCompleted: updateGradientWidth()

    Repeater {
        model: label.shadowEnabled && !label.paintedMode ? 9 : 0
        Text {
            required property int index
            width: label.width
            x: label.shadowOffsetX + (index === 0 ? 0 : Math.cos((index - 1) * Math.PI / 4) * label.shadowBlur * .45)
            y: label.shadowOffsetY + (index === 0 ? 0 : Math.sin((index - 1) * Math.PI / 4) * label.shadowBlur * .45)
            text: content.text
            font: content.font
            horizontalAlignment: content.horizontalAlignment
            wrapMode: content.wrapMode
            elide: content.elide
            color: label.shadowColor
            opacity: index === 0 ? .5 : .12
        }
    }
    Repeater {
        model: label.outlineWidth > 1 && !label.paintedMode ? 16 : 0
        Text {
            required property int index
            width: label.width
            x: Math.cos(index * Math.PI / 8) * Math.max(0, label.outlineWidth - .7)
            y: Math.sin(index * Math.PI / 8) * Math.max(0, label.outlineWidth - .7)
            text: content.text
            font: content.font
            horizontalAlignment: content.horizontalAlignment
            wrapMode: content.wrapMode
            elide: content.elide
            color: label.outlineColor
            style: Text.Outline
            styleColor: label.outlineColor
        }
    }
    Text {
        id: content
        visible: !label.paintedMode
        width: label.width
        color: label.textColor
        style: label.outlineWidth > 0 ? Text.Outline : Text.Normal
        styleColor: label.outlineColor
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignHCenter
    }
    GradientLyricItem {
        id: gradientGlyphs
        objectName: "gradientLyricGlyphs"
        visible: label.paintedMode
        readonly property real effectPadding: Math.max(2, label.outlineWidth + label.shadowBlur + Math.max(Math.abs(label.shadowOffsetX), Math.abs(label.shadowOffsetY)) + 2)
        x: -effectPadding
        y: -effectPadding
        onEffectPaddingChanged: label.updateGradientWidth()
        height: textHeight + effectPadding * 2
        padding: effectPadding
        text: label.paintedMode ? content.text : ""
        font: content.font
        textAlignment: content.horizontalAlignment
        wrap: content.wrapMode !== Text.NoWrap
        elideMode: content.elide
        textColor: label.textColor
        gradientColor: label.gradientColor
        gradientEnabled: label.gradientEnabled
        sweepEnabled: label.sweepEnabled
        sweepCursor: label.sweepCursor
        sweepProgress: label.sweepProgress
        sweepColor: label.sweepColor
        effectSettings: label.effectSettings
        effectClock: label.effectClock
        outlineColor: label.outlineColor
        outlineWidth: label.outlineWidth
        shadowEnabled: label.shadowEnabled
        shadowColor: label.shadowColor
        shadowBlur: label.shadowBlur
        shadowOffsetX: label.shadowOffsetX
        shadowOffsetY: label.shadowOffsetY
    }
}
