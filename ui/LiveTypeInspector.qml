import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: inspector
    required property var settings
    readonly property bool editableShadow: !!settings.shadowEnabled && !(settings.lineMode === "karaoke" && settings.outputMode !== "dynamic")
    required property bool duet
    signal presetsRequested()
    readonly property var fonts: ["PingFang SC", "Heiti SC", "Songti SC", "Helvetica Neue", "Arial"]
    readonly property var effects: ["none", "dissolve", "ripple", "rgb-split", "glitch"]
    clip: true
    contentWidth: availableWidth
    component Adjust: StyleSlider { settings: inspector.settings; controlPrefix: "live-" }
    component NumberField: ColumnLayout {
        id: numberField
        required property string title
        required property string field
        required property real low
        required property real high
        property int precision: 0
        required property real fallback
        Layout.fillWidth: true; spacing: 3
        Text { text: numberField.title; font.pixelSize: 11; color: Theme.secondary }
        TextField {
            id: entry
            Layout.fillWidth: true; Layout.preferredWidth: 1; implicitHeight: 30
            objectName: "liveValue-" + numberField.field; Accessible.name: numberField.title
            selectByMouse: true; color: Theme.text; font.family: Theme.numberFont
            validator: DoubleValidator { bottom: numberField.low; top: numberField.high; decimals: numberField.precision; notation: DoubleValidator.StandardNotation; locale: "C" }
            function settingText() { return String(inspector.settings[numberField.field] ?? numberField.fallback); }
            Binding { target: entry; property: "text"; value: entry.settingText(); when: !entry.activeFocus; restoreMode: Binding.RestoreNone }
            onEditingFinished: { if (acceptableInput && text !== settingText()) controller.setStyle(numberField.field, Number(text)); text = settingText(); }
            background: Rectangle { radius: 5; color: Theme.elevated; border.color: entry.activeFocus ? Theme.focus : Theme.stroke }
        }
    }
    component Choice: DarkComboBox {
        required property string key
        Layout.fillWidth: true; implicitWidth: 80
        model: ["无", "柔和叠化", "涟漪波纹", "色差分离", "数字故障"]
        currentIndex: Math.max(0, inspector.effects.indexOf(inspector.settings[key] || "none"))
        onActivated: function(index) { controller.setStyle(key, inspector.effects[index]); }
    }
    ColumnLayout {
        width: inspector.availableWidth
        spacing: 7
        LiveControlGroup {
            title: "换歌沿用当前设置"; surface: "#25222f"
            Adjust { title: "歌词亮度"; field: "brightness"; percentage: true; minimum: .35; maximum: 1; interval: .01; fallback: 1 }
        }
        LiveControlGroup {
            StudioButton { id: placement; text: checked ? "收起歌词字幕位置" : "展开歌词字幕位置"; iconName:"tools"; checkable:true; checked:false; Layout.fillWidth:true; textAlignment:Text.AlignLeft }
            Adjust { visible: placement.checked; title: "垂直位置"; field: "verticalPosition"; minimum: 8; maximum: 90; fallback: 56; suffix: "%" }
        }
        LiveControlGroup {
            title: "字体"
            DarkComboBox {
                objectName: "liveFontCombo"; Layout.fillWidth: true; Accessible.name: "字幕字体"
                model: ["苹方", "黑体", "宋体", "Helvetica Neue", "Arial"]
                currentIndex: Math.max(0, inspector.fonts.indexOf(inspector.settings.fontFamily || "PingFang SC"))
                onActivated: function(index) { controller.setStyle("fontFamily", inspector.fonts[index]); }
            }
            RowLayout {
                Layout.fillWidth:true
                NumberField { title:"字号"; field:"fontSize"; low:24; high:96; fallback:48 }
                NumberField { title:"字距"; field:"letterSpacing"; low:0; high:8; precision:1; fallback:0 }
                NumberField { title:"行距"; field:"lineSpacing"; low:0; high:64; fallback:23 }
            }
        }
        LiveControlGroup {
            title: "扫色方式"
            DarkCheckBox { objectName: "liveLyricSweep"; text: "歌词走带 · 跟唱扫色"; checked: inspector.settings.sweepMode !== "sentence"; onClicked: controller.setLyricSweep(checked); Accessible.onToggleAction: controller.setLyricSweep(inspector.settings.sweepMode === "sentence") }
            DarkCheckBox { objectName: "liveDuetColors"; text: "对唱分色"; checked: inspector.duet; onClicked: controller.setDuetColors(checked) }
            ActionButton {
                objectName: "liveStyleSettings"; Layout.fillWidth: true; text: "字幕特效"; hint: "选择颜色预设或调整字幕效果"
                background: Rectangle { radius: 6; border.color: parent.activeFocus ? Theme.focus : Theme.hairlineStrong; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "#8525a3" } GradientStop { position: 1; color: "#226fa5" } } }
                onClicked: inspector.presetsRequested()
            }
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout { Layout.fillWidth: true; Text { text: "入场效果"; color: Theme.secondary; font.pixelSize: 11 } Choice { key: "liveEntrance"; Accessible.name: "入场效果" } }
                ColumnLayout { Layout.fillWidth: true; Text { text: "退场效果"; color: Theme.secondary; font.pixelSize: 11 } Choice { key: "liveExit"; Accessible.name: "退场效果" } }
            }
        }
        LiveControlGroup {
            title: "文字描边"
            Adjust { title: "描边粗细"; field: "outlineWidth"; minimum: 0; maximum: 8; interval: .5; fallback: 1; suffix: " px" }
            StyleColorControl { compact:true; prefix:"live-"; Layout.fillWidth: true; label: "描边颜色"; styleKey: "outlineColor"; colorValue: inspector.settings.outlineColor || "#000000" }
        }
        LiveControlGroup {
            title: "文字阴影"
            DarkCheckBox { text: "启用阴影"; checked: !!inspector.settings.shadowEnabled; onClicked: controller.setStyle("shadowEnabled", checked) }
            StyleColorControl { visible:inspector.editableShadow; compact:true; prefix:"live-"; Layout.fillWidth:true; label:"阴影颜色"; styleKey:"shadowColor"; colorValue:inspector.settings.shadowColor || "#000000" }
            Adjust { visible:inspector.editableShadow; title:"模糊度"; field:"shadowBlur"; minimum:0; maximum:16; fallback:6; suffix:" px" }
            Adjust { visible:inspector.editableShadow; title:"水平偏移"; field:"shadowOffsetX"; minimum:-12; maximum:12; suffix:" px" }
            Adjust { visible:inspector.editableShadow; title:"垂直偏移"; field:"shadowOffsetY"; minimum:-12; maximum:12; fallback:3; suffix:" px" }
        }
    }
}
