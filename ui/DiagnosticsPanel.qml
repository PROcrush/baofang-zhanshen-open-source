import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    objectName: "diagnosticsPanel"
    property var stateData: ({})
    readonly property var diagnostics: stateData.diagnostics || ({})
    function checkLabel(s) { return ({ok:"通过", pass:"通过", ready:"就绪", warning:"需确认", warn:"需确认", error:"失败", failed:"失败", fail:"失败", unavailable:"未核验", pending:"待核验"})[s] || s || "未核验"; }
    function checkTone(s) { return ["ok", "pass", "ready", "通过", "就绪"].indexOf(s) >= 0 ? "green" : ["error", "fail", "failed", "失败"].indexOf(s) >= 0 ? "red" : "amber"; }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pagePadding
        anchors.rightMargin: Theme.pagePadding
        anchors.topMargin: 14
        anchors.bottomMargin: 12
        spacing: 14

        // 歌暮式顶部工具栏行：左侧标题 + 说明，主操作在右
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text { text: "接入证据与运行状态"; color: Theme.text; font.pixelSize: Theme.fontSection; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; Accessible.role: Accessible.Heading; Accessible.name: text }
                Text { Layout.fillWidth: true; text: panel.diagnostics.summary || "尚未运行本机探针"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
            }
            StudioButton { objectName: "diagnosticsExport"; text: "导出诊断"; implicitHeight: 34; onClicked: controller.exportDiagnostics() }
            ActionButton { objectName: "diagnosticsProbe"; text: "运行只读探针"; tone: "primary"; implicitHeight: 34; onClicked: controller.runProbe() }
        }

        // 提示条：正常态不铺色，只用细边提示
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: probeHint.implicitHeight + 24
            radius: Theme.radiusControl
            color: "transparent"
            border.color: Theme.hairline
            Text {
                id: probeHint
                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14; anchors.rightMargin: 14
                text: "探针用于核查本机软件、候选文件和字段证据。发现文件或进程，仍不足以证明实时播放数据已接通。"
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
        }

        ScrollView {
            objectName: "diagnosticsScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Column {
                width: parent.width
                spacing: 10
                Repeater {
                    model: panel.diagnostics.checks || []
                    delegate: Rectangle {
                        required property var modelData
                        width: parent.width
                        height: Math.max(84, detail.implicitHeight + 56)
                        radius: Theme.radiusCard
                        color: Theme.surface
                        border.color: Theme.hairline
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 15
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: modelData.label || "检查项"; color: Theme.text; font.pixelSize: 14; font.weight: Font.Medium; Layout.fillWidth: true }
                                StatusPill { text: panel.checkLabel(modelData.status); tone: panel.checkTone(modelData.status || "") }
                            }
                            Text { id: detail; Layout.fillWidth: true; text: modelData.detail || ""; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap; lineHeight: 1.3 }
                        }
                    }
                }
            }
        }
    }
}
