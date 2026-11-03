import QtQuick

// Zenox 式点缀：一条短渐变横条，带一点辉光。用于选中标签和栏标题。
Item {
    id: bar
    property bool vertical: false
    implicitWidth: vertical ? 3 : 24
    implicitHeight: vertical ? 20 : 3
    Rectangle {
        anchors.centerIn: parent
        width: parent.width + 6; height: parent.height + 6; radius: height / 2
        color: Theme.glow; opacity: 0.6
    }
    Rectangle {
        anchors.fill: parent; radius: Math.min(width, height) / 2
        gradient: Gradient {
            orientation: bar.vertical ? Gradient.Vertical : Gradient.Horizontal
            GradientStop { position: 0; color: Theme.gradientStart }
            GradientStop { position: 1; color: Theme.gradientEnd }
        }
    }
}
