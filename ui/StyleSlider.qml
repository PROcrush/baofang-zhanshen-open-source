import QtQuick
import QtQuick.Layouts

RowLayout {
    required property var settings
    property string controlPrefix: "style-"
    id: row
    property string title: ""
    property string field: "fontSize"
    property real minimum: 0
    property real maximum: 100
    property real interval: 1
    property real fallback: 0
    property bool percentage: false
    property string suffix: ""
    readonly property real settingValue: settings[field] === undefined ? fallback : Number(settings[field])
    Layout.fillWidth: true
    spacing: 12
    Text { text: row.title; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
    ControlSlider {
        objectName: row.controlPrefix + row.field + "Slider"
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
        text: row.percentage ? Math.round(row.settingValue * 100) + "%" : (row.interval < 1 ? row.settingValue.toFixed(row.interval < .1 ? 2 : 1) : Math.round(row.settingValue)) + row.suffix
        color: Theme.accent
        font.family: Theme.numberFont
        font.pixelSize: 11
    }
}
