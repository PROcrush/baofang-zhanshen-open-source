import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    objectName: "outputStylePanel"
    property var stateData: ({})
    readonly property var settings: stateData.style || ({})
    readonly property bool traditional: settings.lineMode === "karaoke" && settings.outputMode !== "dynamic"
    readonly property var fontValues: ["PingFang SC", "Heiti SC", "Songti SC", "Helvetica Neue", "Arial"]
    property bool sampleRequested: false
    property real samplePosition: 1800
    property bool samplePlaying: false
    property bool libraryVisible: true
    readonly property var effectNames: ["无", "柔和叠化", "涟漪波纹", "色差分离", "数字故障"]
    readonly property var effectValues: ["none", "dissolve", "ripple", "rgb-split", "glitch"]
    function previewEffects() { sampleRequested=true; samplePosition=0; samplePlaying=true; }
    function openEffects() { libraryVisible=true; presetLibrary.resetFilters(); }
    readonly property bool previewUsesSample: sampleRequested || !(stateData.lyric && stateData.lyric.visible)
    readonly property var previewState: {
        if (!previewUsesSample) return stateData
        var sample = Object.assign({}, stateData)
        sample.selectedSource = "A"
        sample.effectiveSource = "A"
        sample.stale = false
        sample.outputBlocked = false
        sample.inputMode = "style-preview"
        sample.syntheticUIFrame = false
        sample.deckA = {cacheState: "ready"}
        var current = {text: "灯光照亮每一次相聚", startMs: 0, endMs: 4000}
        var following = {text: "让熟悉的旋律陪伴此刻", startMs: 4000, endMs: 8000}
        sample.lyric = {visible: true, text: current.text, nextText: following.text,
                       progress: samplePosition/4000, quality: "sentence", sweepTiming: "sentence",
                       sweepCursor: [0, current.text.length, samplePosition/4000],
                       effectClock: {startMs: 0, endMs: 4000, positionMs: samplePosition},
                       karaoke: {current: current, following: following, positionMs: samplePosition,
                                 appearanceMs: 0, followingAppearanceMs: 0, waiting: false, lineIndex: 0}}
        return sample
    }
    Timer {
        interval: 33; repeat: true
        running: panel.visible && panel.sampleRequested && panel.samplePlaying
        onTriggered: {
            panel.samplePosition += interval
            if (panel.samplePosition >= 4000) { panel.samplePlaying=false; panel.samplePosition=1800; }
        }
    }

    component EffectChoice: RowLayout {
        property string title: ""
        property string field: "liveEntrance"
        spacing: 8
        Text { text: parent.title; color: Theme.secondary; font.pixelSize: 12 }
        DarkComboBox {
            objectName: "style-" + parent.field
            Layout.preferredWidth: 142
            implicitHeight: 34
            model: panel.effectNames
            currentIndex: Math.max(0, panel.effectValues.indexOf(panel.settings[parent.field] || "none"))
            Accessible.name: parent.title
            onActivated: function(index) { controller.setStyle(parent.field, panel.effectValues[index]); panel.previewEffects(); }
        }
    }

    component SliderSetting: RowLayout {
        id: row
        property string title: ""
        property string field: "fontSize"
        property real minimum: 0
        property real maximum: 100
        property real interval: 1
        property real fallback: 0
        property string suffix: ""
        readonly property real settingValue: panel.settings[field] === undefined ? fallback : Number(panel.settings[field])
        Layout.fillWidth: true
        spacing: 12
        Text { text: row.title; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
        ControlSlider {
            objectName: "style-" + row.field + "Slider"
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            from: row.minimum
            to: row.maximum
            stepSize: row.interval
            value: row.settingValue
            onMoved: controller.setStyle(row.field, Math.round(value * 100) / 100)
            Accessible.name: "字幕" + row.title
        }
        Text {
            Layout.preferredWidth: 63
            horizontalAlignment: Text.AlignRight
            text: (row.interval < 1 ? row.settingValue.toFixed(row.interval < .1 ? 2 : 1) : Math.round(row.settingValue)) + row.suffix
            color: Theme.accent
            font.family: Theme.numberFont
            font.pixelSize: 11
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: panel.width < 900 ? 16 : 24
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                spacing: 4
                Text { text: "现场字幕特效"; color: Theme.text; font.pixelSize: 20; font.weight: Font.Medium }
                Text { text: "修改即显示，预览、客屏与 Serato 叠加共用同一份设置。"; color: Theme.secondary; font.pixelSize: 12 }
            }
            Item { Layout.fillWidth: true }
            StatusPill { text: "自动保存"; tone: "blue" }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: panel.width < 1100 ? 160 : 185
            radius: 18
            color: Theme.surface
            border.color: Theme.stroke
            RowLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 16
                ProgramView {
                    id: stylePreview
                    objectName: "style-livePreview"
                    Layout.fillHeight: true
                    Layout.preferredWidth: height * 16 / 9
                    Layout.maximumWidth: panel.width < 1100 ? 280 : 330
                    stateData: panel.previewState
                    compact: true
                    radius: 10
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 9
                    Text { text: panel.previewUsesSample ? "示例预览" : "正在预览现场歌词"; color: Theme.text; font.pixelSize: 14; font.weight: Font.Medium }
                    Text {
                        Layout.fillWidth: true
                        text: panel.previewUsesSample ? "示例仅在这里显示，不会发到客屏。字幕跟随实际播放位置。" : "这里的每次调整会同步到已打开的输出窗口。"
                        wrapMode: Text.Wrap
                        color: Theme.secondary
                        font.pixelSize: 12
                        lineHeight: 1.4
                    }
                    Text {
                        objectName: "style-syncQualityNote"
                        Layout.fillWidth: true
                        text: panel.settings.sweepMode === "sentence" ? "走带已关闭，按演唱时间自动换句。" : panel.previewState.lyric && panel.previewState.lyric.quality === "sentence" ? "按句估算扫色；有词时码时按词跟唱。" : "跟唱扫色优先使用已有词时码，暂停和跳点跟随源位置。"
                        color: Theme.amber
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        ActionButton { objectName: "style-previewToggle"; text: panel.sampleRequested ? "跟随现场歌词" : "使用示例歌词"; onClicked: { panel.samplePlaying=false; panel.sampleRequested = !panel.sampleRequested; } }
                        ActionButton { objectName: "style-previewEffects"; text: "预演效果"; onClicked: panel.previewEffects() }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 18
            EffectChoice { title: "入场效果"; field: "liveEntrance" }
            EffectChoice { title: "退场效果"; field: "liveExit" }
            Item { Layout.fillWidth: true }
            Text { text: "自动时长 · 走带独立控制"; color: Theme.muted; font.pixelSize: 11 }
        }
        Text {
            objectName: "style-effectError"
            Layout.fillWidth: true
            visible: text.length > 0
            text: String(panel.stateData.notice || "").indexOf("字幕特效未保存：") === 0 ? panel.stateData.notice : ""
            wrapMode: Text.Wrap
            color: Theme.red
            font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            ActionButton { objectName: "style-libraryTab"; text: "样式库"; selected: panel.libraryVisible; onClicked: panel.libraryVisible = true }
            ActionButton { objectName: "style-adjustTab"; text: "微调样式"; selected: !panel.libraryVisible; onClicked: panel.libraryVisible = false }
            Item { Layout.fillWidth: true }
            Text { text: "点击样式即可应用"; visible: panel.libraryVisible; color: Theme.secondary; font.pixelSize: 12 }
        }
        StylePresetLibrary {
            id: presetLibrary
            objectName: "style-presetLibrary"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: panel.libraryVisible
            stateData: panel.stateData
            onAdjustRequested: panel.libraryVisible = false
        }
        ScrollView {
            id: settingsScroll
            objectName: "style-settingsScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !panel.libraryVisible
            contentWidth: availableWidth
            clip: true
            ScrollBar.vertical.policy: ScrollBar.AlwaysOn
            ColumnLayout {
                width: settingsScroll.availableWidth
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: "一键预设"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
                    ActionButton { objectName: "style-preset-classic"; text: "传统 KTV"; onClicked: controller.setLiveLayout("karaoke") }
                    ActionButton { objectName: "style-preset-gold"; text: "暖金双行"; onClicked: controller.setStylePreset("gold") }
                    ActionButton { objectName: "style-preset-neon"; text: "霓虹扫色"; onClicked: controller.setStylePreset("neon") }
                    ActionButton { objectName: "style-preset-minimal"; text: "简洁单行"; onClicked: controller.setStylePreset("minimal") }
                    Item { Layout.fillWidth: true }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: modeControls.implicitHeight + 20
                    color: Theme.surface
                    radius: 12
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: modeControls
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "显示布局"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
                            ActionButton { objectName: "style-single"; text: "单行"; selected: panel.settings.lineMode === "single"; implicitHeight: 32; onClicked: { controller.setStyle("lineMode", "single"); controller.setStyle("outputMode", "standard") } }
                            ActionButton { objectName: "style-standard"; text: "简洁双行"; selected: panel.settings.lineMode !== "single" && panel.settings.lineMode !== "karaoke" && panel.settings.outputMode !== "dynamic"; implicitHeight: 32; onClicked:controller.setLiveLayout("double") }
                            ActionButton { objectName: "style-karaoke"; text: "传统 KTV"; selected: panel.settings.lineMode === "karaoke" && panel.settings.outputMode !== "dynamic"; implicitHeight: 32; onClicked: controller.setLiveLayout("karaoke") }
                            ActionButton { objectName: "style-dynamic"; text: "动态大字"; selected: panel.settings.outputMode === "dynamic"; implicitHeight: 32; onClicked: controller.setStyle("outputMode", "dynamic") }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "高亮方式"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
                            ActionButton { objectName: "style-sentence"; text: "关闭走带"; selected: panel.settings.sweepMode === "sentence"; implicitHeight: 30; onClicked: controller.setLyricSweep(false) }
                            ActionButton { objectName: "style-word"; text: "歌词走带"; selected: panel.settings.sweepMode !== "sentence"; implicitHeight: 30; onClicked: controller.setLyricSweep(true) }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: typography.implicitHeight + 32
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: typography
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 9
                        Text { text: "字体与大小"; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "字体"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
                            DarkComboBox {
                                objectName: "style-fontCombo"
                                model: ["苹方", "黑体", "宋体", "Helvetica Neue", "Arial"]
                                currentIndex: Math.max(0, panel.fontValues.indexOf(panel.settings.fontFamily || "PingFang SC"))
                                onActivated: controller.setStyle("fontFamily", panel.fontValues[index])
                            }
                            DarkCheckBox { objectName: "style-fontBoldToggle"; text: "加粗"; checked: panel.settings.fontBold !== false; onClicked: controller.setStyle("fontBold", checked); palette.windowText: Theme.secondary }
                            Item { Layout.fillWidth: true }
                        }
                        SliderSetting { title: "字号"; field: "fontSize"; minimum: 24; maximum: 96; fallback: 48; suffix: " px" }
                        SliderSetting { title: "字距"; field: "letterSpacing"; minimum: 0; maximum: 8; interval: .5; suffix: " px" }
                        SliderSetting { title: "行距"; field: "lineSpacing"; minimum: 0; maximum: 64; fallback: 23; suffix: " px" }
                        SliderSetting { title: "亮度"; field: "brightness"; minimum: .35; maximum: 1; interval: .01; fallback: 1 }
                        Text { text: "字号和间距随输出窗口等比例缩放；长句自动换行并适配。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: colorControls.implicitHeight + 32
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: colorControls
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10
                        Text { text: "文字颜色与预设效果"; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium }
                        StyleColorControl { Layout.fillWidth: true; label: "普通文字"; styleKey: "baseColor"; colorValue: panel.settings.baseColor || "#F0F1F3" }
                        StyleColorControl { Layout.fillWidth: true; label: "扫色高亮"; styleKey: "highlightColor"; colorValue: panel.settings.highlightColor || "#F0C17A" }
                        StyleColorControl { Layout.fillWidth: true; label: "下一句"; styleKey: "nextColor"; colorValue: panel.settings.nextColor || "#929AA7"; visible: !panel.traditional }
                        DarkCheckBox { objectName: "style-gradientToggle"; text: "文字渐变"; checked: !!panel.settings.gradientEnabled; onClicked: controller.setStyle("gradientEnabled", checked); palette.windowText: Theme.secondary; visible: !panel.traditional }
                        StyleColorControl { Layout.fillWidth: true; label: "渐变末色"; styleKey: "gradientColor"; colorValue: panel.settings.gradientColor || "#F0F1F3"; visible: !panel.traditional && !!panel.settings.gradientEnabled }
                        DarkCheckBox { objectName: "style-decorationToggle"; text: "显示预设装饰"; checked: panel.settings.decorationEnabled !== false; onClicked: controller.setStyle("decorationEnabled", checked); palette.windowText: Theme.secondary; visible: !panel.traditional }
                        StyleColorControl { Layout.fillWidth: true; label: "装饰颜色"; styleKey: "decorationColor"; colorValue: panel.settings.decorationColor || "#16F4D4"; visible: !panel.traditional && panel.settings.decorationEnabled !== false && !!panel.settings.decorationMode && panel.settings.decorationMode !== "none" }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: effectControls.implicitHeight + 32
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: effectControls
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10
                        Text { text: "描边与阴影"; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium }
                        StyleColorControl { Layout.fillWidth: true; label: "描边颜色"; styleKey: "outlineColor"; colorValue: panel.settings.outlineColor || "#070B12" }
                        SliderSetting { title: "描边宽度"; field: "outlineWidth"; minimum: 0; maximum: 8; interval: .5; fallback: 1; suffix: " px" }
                        DarkCheckBox { objectName: "style-shadowToggle"; text: "启用文字阴影"; checked: !!panel.settings.shadowEnabled; onClicked: controller.setStyle("shadowEnabled", checked); palette.windowText: Theme.secondary }
                        StyleColorControl { Layout.fillWidth: true; label: "阴影颜色"; styleKey: "shadowColor"; colorValue: panel.settings.shadowColor || "#000000"; visible: !panel.traditional }
                        SliderSetting { title: "阴影柔度"; field: "shadowBlur"; minimum: 0; maximum: 16; fallback: 6; suffix: " px"; visible: !panel.traditional }
                        SliderSetting { title: "水平偏移"; field: "shadowOffsetX"; minimum: -12; maximum: 12; suffix: " px"; visible: !panel.traditional }
                        SliderSetting { title: "垂直偏移"; field: "shadowOffsetY"; minimum: -12; maximum: 12; fallback: 3; suffix: " px"; visible: !panel.traditional }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: placement.implicitHeight + 32
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: placement
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10
                        Text { text: "屏幕位置"; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium }
                        SliderSetting { title: "垂直位置"; field: "verticalPosition"; minimum: 8; maximum: 90; fallback: 56; suffix: "%" }
                        SliderSetting { title: "左右边距"; field: "horizontalMargin"; minimum: 2; maximum: 20; fallback: 4; suffix: "%" }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "对齐"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
                            visible: !panel.traditional
                            ActionButton { objectName: "style-align-left"; text: "靠左"; selected: panel.settings.textAlignment === "left"; onClicked: controller.setStyle("textAlignment", "left") }
                            ActionButton { objectName: "style-align-center"; text: "居中"; selected: !panel.settings.textAlignment || panel.settings.textAlignment === "center"; onClicked: controller.setStyle("textAlignment", "center") }
                            ActionButton { objectName: "style-align-right"; text: "靠右"; selected: panel.settings.textAlignment === "right"; onClicked: controller.setStyle("textAlignment", "right") }
                            Item { Layout.fillWidth: true }
                        }
                        Text { text: panel.traditional ? "传统 KTV 固定上左、下右轮唱，两行使用相同字形和普通文字颜色。" : "靠近屏幕边缘时会保留安全留白，避免字幕被切掉。"; color: Theme.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: backgrounds.implicitHeight + 32
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.stroke
                    ColumnLayout {
                        id: backgrounds
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10
                        Text { text: "独立客屏背景"; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium }
                        DarkCheckBox { objectName: "style-bgToggle"; text: "显示样式背景"; checked: !!panel.settings.dynamicBackground; onClicked: controller.setStyle("dynamicBackground", checked); palette.windowText: Theme.secondary }
                        StyleColorControl { Layout.fillWidth: true; label: "背景颜色"; styleKey: "backgroundColor"; colorValue: panel.settings.backgroundColor || "#000000" }
                        RowLayout {
                            ActionButton { text: "暖金"; selected: panel.stateData.scene === "amber" || !panel.stateData.scene; onClicked: controller.setScene("amber") }
                            ActionButton { text: "深蓝"; selected: panel.stateData.scene === "blue"; onClicked: controller.setScene("blue") }
                            ActionButton { text: "纯黑"; selected: panel.stateData.scene === "black"; onClicked: controller.setScene("black") }
                        }
                        Text { Layout.fillWidth: true; text: "此处背景用于独立客屏。Serato 叠加只显示透明歌词，保留原视频画面。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
                        RowLayout {
                            DarkCheckBox { objectName: "appearance-reduceMotion"; text: "减少界面动态"; checked: Theme.reduceMotion; onClicked: Theme.reduceMotion = checked; palette.windowText: Theme.secondary }
                            DarkCheckBox { objectName: "appearance-reduceTransparency"; text: "降低界面透明度"; checked: Theme.reduceTransparency; onClicked: Theme.reduceTransparency = checked; palette.windowText: Theme.secondary }
                        }
                    }
                }
            }
        }
    }
}
