import QtQuick
import QtQuick.Layouts

// 编号步骤标题：圆形序号 + 标题；done 时序号换成对勾。
RowLayout {
    id: step
    property int number: 1
    property string title: ""
    property bool done: false
    spacing: 10
    PillFrame {
        Layout.preferredWidth: 24; Layout.preferredHeight: 24
        accentRing: !step.done
        fill: step.done ? Theme.greenSoft : Theme.accentSoft
        line: Theme.greenLine
        Text { anchors.centerIn: parent; text: step.done ? "✓" : String(step.number); color: step.done ? Theme.green : Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
    }
    Text { text: step.title; color: Theme.text; font.pixelSize: Theme.fontSection; font.weight: Font.Medium; Accessible.role: Accessible.Heading; Accessible.name: "第 " + step.number + " 步，" + step.title }
}
