import QtQuick
import Baofang.Lyric 1.0

Rectangle {
    id: stage
    objectName: "programView"
    property var stateData: ({})
    property bool compact: false
    property bool presetThumbnail: false
    property bool transparentStage: false
    readonly property var lyrics: stateData.lyric || ({})
    readonly property var settings: stateData.style || ({})
    readonly property bool replay: stateData.inputMode ? stateData.inputMode === "replay" : stateData.mode === "replay"
    readonly property var selectedDeck: stateData.effectiveSource === "B" ? (stateData.deckB || ({})) : (stateData.deckA || ({}))
    readonly property bool sourceReady: stateData.effectiveSource !== "none" && stateData.selectedSource !== "none" && !stateData.outputBlocked && (!selectedDeck.cacheState || selectedDeck.cacheState === "ready")
    readonly property bool showLyrics: !!lyrics.visible && !stateData.stale && sourceReady
    readonly property bool dynamicMode: settings.outputMode === "dynamic"
    readonly property bool singleLine: dynamicMode || settings.lineMode === "single"
    readonly property bool traditionalMode: !dynamicMode && settings.lineMode === "karaoke"
    readonly property real viewScale: Math.max(.08, width / 1120)
    readonly property int lyricAlignment: settings.textAlignment === "left" ? Text.AlignLeft : settings.textAlignment === "right" ? Text.AlignRight : Text.AlignHCenter
    readonly property real horizontalMargin: settings.horizontalMargin === undefined ? 4 : settings.horizontalMargin
    readonly property real verticalPosition: settings.verticalPosition === undefined ? (dynamicMode ? 46 : 56) : settings.verticalPosition
    readonly property real outlineWidth: (settings.outlineWidth === undefined ? 1 : settings.outlineWidth) * viewScale
    readonly property bool renderWordSweep: traditionalMode ? !!karaokeLayer && karaokeLayer.canSweep : settings.sweepMode !== "sentence" && (!!lyrics.sweepCursor || lyrics.quality === "word")
    readonly property bool coolScene: stateData.scene === "blue"
    readonly property bool backgroundVisible: !transparentStage && !!settings.dynamicBackground && stateData.scene !== "black"
    readonly property color accent: settings.highlightColor || (coolScene ? "#86bde0" : "#f0c17a")
    readonly property color presetBackground: settings.backgroundColor || (coolScene ? "#102033" : "#231b16")
    readonly property color decorationColor: settings.decorationColor || accent
    readonly property string decorationMode: settings.decorationMode || "none"
    readonly property bool colorShift: settings.decorationEnabled !== false && (decorationMode === "noir" || decorationMode === "magenta" || decorationMode === "mono")
    readonly property string lyricFontFamily: settings.fontFamily || "PingFang SC"
    readonly property real lyricSpacing: (settings.letterSpacing || 0) * viewScale
    readonly property real lyricBrightness: Math.max(0.35, Math.min(1, settings.brightness === undefined ? 1 : settings.brightness))
    readonly property real desiredLyricSize: (settings.fontSize || 48) * (dynamicMode ? 1.2 : 1) * viewScale
    readonly property real fittedLyricSize: Math.min(desiredLyricSize, Math.max(8 * viewScale,
        Math.sqrt(width * (1 - horizontalMargin / 50) * height * 0.29 * desiredLyricSize / Math.max(1, naturalMetrics.advanceWidth * 1.5))))
    readonly property real fittedLyricSpacing: lyricSpacing * fittedLyricSize / Math.max(1, desiredLyricSize)
    property real ambientShift: 0
    // 对唱分色：只改未唱文字颜色，扫色不变
    readonly property string singer: stateData.duetColors ? (lyrics.singer || "") : ""
    readonly property color singerColor: singer === "male" ? "#6EC6FF" : singer === "female" ? "#FF8FC8" : singer === "chorus" ? "#8EE6A0" : (settings.baseColor || "#F0F1F3")
    readonly property var lyricSettings: singer ? Object.assign({}, settings, {baseColor: String(singerColor)}) : settings
    readonly property var guestMessage: stateData.guestMessage || ({})
    readonly property var guestQr: stateData.guestQr || ({})
    color: transparentStage ? "transparent" : "#000000"
    clip: true

    TextMetrics {
        id: naturalMetrics
        text: String(stage.lyrics.text || "").replace(/\n/g, " ")
        font.family: stage.lyricFontFamily
        font.pixelSize: stage.desiredLyricSize
        font.letterSpacing: stage.lyricSpacing
        font.weight: stage.settings.fontBold === false ? Font.Normal : Font.DemiBold
    }

    TextMetrics {
        id: fittedMetrics
        text: stage.lyrics.text || ""
        font.family: stage.lyricFontFamily
        font.pixelSize: stage.fittedLyricSize
        font.letterSpacing: stage.fittedLyricSpacing
        font.weight: stage.settings.fontBold === false ? Font.Normal : Font.DemiBold
    }
    TextMetrics {
        id: nextMetrics
        text: stage.lyrics.nextText || ""
        font.family: stage.lyricFontFamily
        font.pixelSize: Math.max(10 * stage.viewScale, stage.fittedLyricSize * .55)
        font.letterSpacing: stage.fittedLyricSpacing
        font.weight: stage.settings.fontBold === false ? Font.Normal : Font.DemiBold
    }

    Rectangle {
        anchors.fill: parent
        visible: stage.backgroundVisible
        gradient: Gradient {
            GradientStop { position: 0; color: stage.presetBackground }
            GradientStop { position: 0.5; color: stage.settings.backgroundColor ? Qt.darker(stage.presetBackground, 1.1) : "#11171e" }
            GradientStop { position: 1; color: stage.settings.backgroundColor ? Qt.darker(stage.presetBackground, 1.35) : "#050a10" }
        }
    }
    Canvas {
        id: ambient
        visible: stage.backgroundVisible && !stage.presetThumbnail
        x: stage.ambientShift - 12
        y: -12
        width: parent.width + 24
        height: parent.height + 24
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            var glow = ctx.createRadialGradient(width * 0.5, height * 0.2, 0, width * 0.5, height * 0.2, width * 0.55);
            glow.addColorStop(0, stage.coolScene ? "rgba(72, 127, 171, 0.25)" : "rgba(168, 116, 55, 0.24)");
            glow.addColorStop(1, "rgba(0, 0, 0, 0)");
            ctx.fillStyle = glow;
            ctx.fillRect(0, 0, width, height);
            ctx.strokeStyle = stage.coolScene ? "rgba(135, 185, 219, 0.18)" : "rgba(206, 167, 108, 0.17)";
            ctx.lineWidth = 1;
            for (var i = 0; i < 9; i++) {
                var y = height * (0.12 + i * 0.042);
                ctx.beginPath();
                ctx.moveTo(-width * 0.05, y + height * 0.05);
                ctx.bezierCurveTo(width * 0.26, y - height * 0.1, width * 0.68, y + height * 0.15, width * 1.08, y - height * 0.015);
                ctx.stroke();
            }
            ctx.strokeStyle = "rgba(145, 164, 184, 0.045)";
            for (var k = 0; k < 13; k++) {
                ctx.beginPath();
                ctx.moveTo(width * 0.5, height * 0.36);
                ctx.lineTo(width * (k / 12), height);
                ctx.stroke();
            }
        }
    }
    SequentialAnimation on ambientShift {
        running: stage.backgroundVisible && !stage.presetThumbnail
        loops: Animation.Infinite
        NumberAnimation { from: -7; to: 7; duration: 9000; easing.type: Easing.InOutSine }
        NumberAnimation { from: 7; to: -7; duration: 9000; easing.type: Easing.InOutSine }
    }
    onCoolSceneChanged: ambient.requestPaint()

    Text {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: compact ? 16 : 30
        text: "包房战神"
        visible: !stage.transparentStage && !stage.presetThumbnail && !(stage.stateData.screenLayers && stage.stateData.screenLayers.logo.visible && stage.stateData.logoImageUrl)
        color: "#79808b"
        font.family: "PingFang SC"
        font.pixelSize: compact ? 11 : 15
        font.letterSpacing: 3
    }
    Rectangle {
        visible: !stage.transparentStage && !stage.presetThumbnail && (stage.replay || !!stage.stateData.syntheticUIFrame)
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: compact ? 13 : 25
        width: replayCaption.implicitWidth + 20
        height: compact ? 25 : 30
        color: "#30271b"
        border.color: "#645035"
        radius: 5
        Text {
            id: replayCaption
            anchors.centerIn: parent
            text: stage.stateData.syntheticUIFrame ? "合成 UI 测试 · 无声音" : "回放演示 · 无声音"
            color: "#d6b47f"
            font.family: "PingFang SC"
            font.pixelSize: stage.compact ? 10 : 12
        }
    }
    PresetDecoration {
        mode: stage.decorationMode
        visible: stage.showLyrics && !stage.traditionalMode && stage.settings.decorationEnabled !== false && mode !== "none"
        scaleFactor: stage.viewScale
        padding: 18 * stage.viewScale
        readonly property real captionWidth: Math.min(lyricColumn.width, Math.max(lyricLine.width, stage.singleLine ? 0 : nextMetrics.advanceWidth))
        width: captionWidth + 2 * padding
        height: lyricColumn.height + 2 * padding
        x: lyricColumn.x + (stage.lyricAlignment === Text.AlignLeft ? 0 : stage.lyricAlignment === Text.AlignRight ? lyricColumn.width - captionWidth : (lyricColumn.width - captionWidth) / 2) - padding
        y: lyricColumn.y - padding
        accent: stage.accent
        secondary: stage.decorationColor
        background: stage.presetBackground
        foreground: stage.settings.baseColor || "#F0F1F3"
        opacity: stage.lyricBrightness
    }
    Column {
        id: lyricColumn
        objectName: "lyricLayer"
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.max(12 * stage.viewScale, Math.min(parent.height * stage.verticalPosition / 100,
                                                 parent.height - height - 26 * stage.viewScale))
        width: parent.width * (1 - stage.horizontalMargin / 50)
        spacing: (stage.settings.lineSpacing === undefined ? 23 : stage.settings.lineSpacing) * stage.viewScale
        visible: stage.showLyrics && !stage.traditionalMode
        opacity: stage.lyricBrightness
        Item {
            id: lyricLine
            x: stage.lyricAlignment === Text.AlignLeft ? 0 : stage.lyricAlignment === Text.AlignRight ? parent.width - width : (parent.width - width) / 2
            width: Math.min(fittedMetrics.advanceWidth + 2 * stage.outlineWidth, parent.width)
            height: baseLine.implicitHeight
            Repeater {
                model: stage.colorShift ? 2 : 0
                LyricText {
                    required property int index
                    x: (index === 0 ? -1 : 1) * (stage.decorationMode === "magenta" ? 2 : 1.4) * stage.viewScale
                    y: stage.decorationMode === "mono" ? (index === 0 ? -1 : 1) * stage.viewScale : 0
                    width: lyricLine.width
                    height: implicitHeight
                    text: baseLine.text
                    font: baseLine.font
                    horizontalAlignment: baseLine.horizontalAlignment
                    wrapMode: Text.Wrap
                    textColor: index === 0 ? stage.accent : stage.decorationColor
                    outlineWidth: 0
                    effectSettings: stage.settings
                    effectClock: stage.lyrics.effectClock || ({})
                    opacity: .32
                }
            }
            LyricText {
                id: baseLine
                objectName: "currentLyricText"
                width: parent.width
                height: implicitHeight
                text: stage.lyrics.text || ""
                horizontalAlignment: stage.lyricAlignment
                wrapMode: Text.Wrap
                font.family: stage.lyricFontFamily
                font.pixelSize: stage.fittedLyricSize
                font.letterSpacing: stage.fittedLyricSpacing
                font.weight: stage.settings.fontBold === false ? Font.Normal : Font.DemiBold
                textColor: stage.singerColor
                gradientEnabled: !!stage.settings.gradientEnabled
                gradientColor: stage.settings.gradientColor || stage.accent
                outlineColor: stage.settings.outlineColor || "#070B12"
                outlineWidth: stage.outlineWidth
                shadowEnabled: !!stage.settings.shadowEnabled
                shadowColor: stage.settings.shadowColor || "#000000"
                shadowBlur: (stage.settings.shadowBlur === undefined ? 6 : stage.settings.shadowBlur) * stage.viewScale
                shadowOffsetX: (stage.settings.shadowOffsetX || 0) * stage.viewScale
                shadowOffsetY: (stage.settings.shadowOffsetY === undefined ? 3 : stage.settings.shadowOffsetY) * stage.viewScale
                sweepEnabled: !stage.traditionalMode && stage.renderWordSweep
                sweepCursor: stage.lyrics.sweepCursor || []
                sweepProgress: stage.lyrics.progress || 0
                sweepColor: stage.accent
                effectSettings: stage.settings
                effectClock: stage.lyrics.effectClock || ({})
            }
        }
        LyricText {
            objectName: "nextLyricText"
            visible: !stage.singleLine
            width: parent.width
            height: implicitHeight
            text: stage.lyrics.nextText || ""
            horizontalAlignment: stage.lyricAlignment
            elide: Text.ElideRight
            textColor: stage.settings.nextColor || "#929AA7"
            outlineColor: baseLine.outlineColor
            outlineWidth: stage.outlineWidth
            shadowEnabled: baseLine.shadowEnabled
            shadowColor: baseLine.shadowColor
            shadowBlur: baseLine.shadowBlur
            shadowOffsetX: baseLine.shadowOffsetX
            shadowOffsetY: baseLine.shadowOffsetY
            font.family: stage.lyricFontFamily
            font.pixelSize: Math.max(10 * stage.viewScale, stage.fittedLyricSize * .55)
            font.letterSpacing: stage.fittedLyricSpacing
            font.weight: stage.settings.fontBold === false ? Font.Normal : Font.DemiBold
        }
    }
    LiveKaraokeItem {
        id: karaokeLayer
        objectName: "traditionalKaraokeLayer"
        anchors.fill: parent
        lyric: stage.lyrics
        settings: stage.lyricSettings
        visible: stage.showLyrics && stage.traditionalMode
        opacity: stage.lyricBrightness
        Accessible.role: Accessible.StaticText
        Accessible.name: "传统 KTV，" + (stage.settings.sweepMode === "sentence" ? "仅换句" : stage.lyrics.sweepTiming === "sentence" ? "按句估算扫色" : "跟唱扫色") + "，" + (stage.lyrics.text || "")
    }
    Text {
        visible: !stage.transparentStage && !stage.showLyrics && stage.compact
        anchors.centerIn: parent
        text: stage.stateData.selectedSource === "none" ? "歌词已隐藏，背景继续显示" : stage.stateData.stale ? "等待播放状态，歌词暂停显示" : !stage.sourceReady ? "等待此曲歌词，当前仅显示背景" : "间奏中，等待下一句"
        color: "#a6adb7"
        font.family: "PingFang SC"
        font.pixelSize: 16
    }
    Text {
        objectName: "lyricModeNote"
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: stage.compact ? 12 : 26
        visible: !stage.transparentStage && !stage.presetThumbnail && (!stage.traditionalMode || stage.compact) && stage.showLyrics && (!stage.renderWordSweep || stage.lyrics.sweepTiming === "sentence")
        text: stage.traditionalMode && !stage.lyrics.karaoke ? "样式预览" : stage.settings.sweepMode === "sentence" ? "走带已关闭" : stage.lyrics.sweepTiming === "sentence" ? "按句估算扫色" : "句级歌词"
        color: "#757e8a"
        font.family: "PingFang SC"
        font.pixelSize: stage.compact ? 10 : 12
    }
    ScreenLayers { anchors.fill: parent; stateData: stage.stateData; visible: !stage.presetThumbnail }
    Rectangle {
        objectName: "guestMessageBanner"
        visible: !!stage.guestMessage.visible && !stage.presetThumbnail
        anchors.horizontalCenter: parent.horizontalCenter
        y: 30 * stage.viewScale
        width: Math.min(parent.width - 60 * stage.viewScale, guestText.implicitWidth + 56 * stage.viewScale)
        height: guestText.implicitHeight + 22 * stage.viewScale
        radius: height / 2
        color: "#CC1A1420"
        border.color: "#B48CFF"; border.width: Math.max(1, 2 * stage.viewScale)
        Text {
            id: guestText
            anchors.centerIn: parent
            width: parent.width - 40 * stage.viewScale
            horizontalAlignment: Text.AlignHCenter
            text: stage.guestMessage.text || ""
            textFormat: Text.PlainText
            color: "#FFFFFF"; elide: Text.ElideRight
            font.family: "PingFang SC"; font.pixelSize: 34 * stage.viewScale; font.weight: Font.DemiBold
        }
    }
    Rectangle {
        objectName: "guestQrCorner"
        visible: !!stage.guestQr.running && !!stage.guestQr.onScreen && !stage.presetThumbnail
        anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 24 * stage.viewScale
        width: 150 * stage.viewScale; height: width + 30 * stage.viewScale
        radius: 10 * stage.viewScale; color: "#FFFFFF"
        Image {
            anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter; anchors.topMargin: 6 * stage.viewScale
            width: parent.width - 12 * stage.viewScale; height: width
            source: stage.guestQr.image || ""; smooth: false; fillMode: Image.PreserveAspectFit
        }
        Text {
            anchors.bottom: parent.bottom; anchors.bottomMargin: 6 * stage.viewScale; anchors.horizontalCenter: parent.horizontalCenter
            text: "扫码送祝福"; color: "#111111"; font.family: "PingFang SC"; font.pixelSize: 15 * stage.viewScale; font.weight: Font.DemiBold
        }
    }
}
