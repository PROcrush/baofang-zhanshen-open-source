import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: wishes
    property var stateData: ({})
    readonly property var qr: stateData.guestQr || ({})
    readonly property var inbox: stateData.guestInbox || []
    property bool showQr: false
    spacing: 8
    DarkCheckBox { objectName: "wishServerToggle"; text: "开启扫码送祝福"; checked: !!wishes.qr.running; onClicked: controller.setGuestServer(checked) }
    Text { Layout.fillWidth: true; text: "同一 Wi-Fi 扫码发送。由你审核上屏，一次显示一条，15 秒后撤下。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
    StudioButton { objectName: "wishQrSettings"; visible: !!wishes.qr.running; text: wishes.showQr ? "收起二维码 / 设置" : "查看二维码 / 设置"; implicitHeight: 26; font.pixelSize: 11; onClicked: wishes.showQr = !wishes.showQr }
    Image { objectName: "wishQrImage"; visible: !!wishes.qr.running && wishes.showQr; Layout.preferredWidth: 100; Layout.preferredHeight: 100; Layout.alignment: Qt.AlignHCenter; source: wishes.qr.image || ""; fillMode: Image.PreserveAspectFit; smooth: false }
    Text { visible: !!wishes.qr.running && wishes.showQr; Layout.fillWidth: true; text: wishes.qr.url || ""; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.WrapAnywhere }
    DarkCheckBox { objectName: "wishQrOnScreen"; visible: !!wishes.qr.running && wishes.showQr; text: "二维码显示在客屏"; checked: !!wishes.qr.onScreen; onClicked: controller.setGuestQrOnScreen(checked) }
    Text { text: "待上屏 " + wishes.inbox.length + " 条"; color: Theme.text; font.pixelSize: 12 }
    Repeater {
        model: wishes.inbox
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true; spacing: 4
            Text { Layout.fillWidth: true; text: modelData.text; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 12; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: modelData.name || "没留名字"; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 10 }
            RowLayout {
                Layout.fillWidth: true
                StudioButton { objectName: "wishApprove-" + modelData.id; text: "上屏 15 秒"; implicitHeight: 28; font.pixelSize: 11; onClicked: controller.approveGuestWish(modelData.id) }
                StudioButton { objectName: "wishReject-" + modelData.id; text: "不要"; implicitHeight: 28; font.pixelSize: 11; onClicked: controller.rejectGuestWish(modelData.id) }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.hairline }
        }
    }
    Text { visible: wishes.inbox.length === 0; text: "还没有待审祝福"; color: Theme.muted; font.pixelSize: 11 }
}
