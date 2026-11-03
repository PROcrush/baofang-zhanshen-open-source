import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: field
    property string label: ""
    property string fieldName: ""
    property real value: 0
    property real minimum: 0
    property real maximum: 1000000
    property int decimals: 3
    property string unit: ""
    property string editContext: ""
    property int valueRevision: 0
    readonly property string inputText: edit.text
    signal committed(real value)
    Layout.minimumWidth: 0
    spacing: 5
    function sync() { if(edit) edit.text = Number(value).toFixed(decimals); }
    onValueChanged: { valueRevision++; sync(); }
    onEditContextChanged: { valueRevision++; sync(); }
    Component.onCompleted: edit.text = Number(value).toFixed(decimals)
    Text { Layout.fillWidth: true; elide: Text.ElideRight; text: field.label + (field.unit ? "（"+field.unit+"）" : ""); color: Theme.secondary; font.pixelSize: 10 }
    TextField {
        id: edit
        objectName: field.fieldName
        Layout.fillWidth: true; Layout.minimumWidth: 0; implicitWidth: 80; implicitHeight: 31
        selectByMouse: true
        font.family: Theme.numberFont; font.pixelSize: 11
        color: field.enabled ? Theme.text : Theme.muted
        leftPadding: 9; rightPadding: 9
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        validator: DoubleValidator { bottom: field.minimum; top: field.maximum; decimals: field.decimals; locale: "C"; notation: DoubleValidator.StandardNotation }
        Accessible.name: field.label
        onEditingFinished: {
            var number = Number(text);
            if (field.enabled && acceptableInput && isFinite(number)) {
                if (Math.abs(number-Number(field.value.toFixed(field.decimals)))>0.000001) {
                    var revision=field.valueRevision;
                    Qt.callLater(function() { if(field.enabled && revision===field.valueRevision) field.committed(number); });
                }
            } else text = Number(field.value).toFixed(field.decimals);
        }
        background: Rectangle { radius: 8; color: Theme.canvas; border.color: edit.activeFocus ? Theme.focus : "#39424d"; border.width: edit.activeFocus ? 2 : 1 }
    }
}
