import QtQuick

Rectangle {
    property bool vertical: false
    implicitWidth: vertical ? 3 : 24
    implicitHeight: vertical ? 20 : 3
    radius: 1
    color: Theme.accent
}
