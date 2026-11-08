import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: wishes
    property var stateData: ({})
    readonly property var qr: stateData.guestQr || ({})
    property var inbox: []
    readonly property bool publicActive: qr.mode === "public" && !!qr.active
    readonly property bool lanActive: qr.mode !== "public" && !!qr.running
    readonly property bool offline: !!(stateData.offlineLibrary || {}).offlineOnly
    property bool showQr: false
    onStateDataChanged: syncInbox()
    Component.onCompleted: syncInbox()

    function syncInbox() {
        var rows = stateData.guestInbox || [];
        if (rows.length !== inbox.length || rows.some(function(row, index) { return row.id !== inbox[index].id; }))
            inbox = rows;
    }

    spacing: 8
    DarkCheckBox { objectName: "wishServerToggle"; text: "局域网扫码"; checked: wishes.lanActive; enabled: !wishes.publicActive; onClicked: controller.setGuestServer(!wishes.lanActive) }
    DarkCheckBox { objectName: "wishPublicToggle"; text: "公网扫码（支持手机流量）"; checked: wishes.publicActive; enabled: !!wishes.stateData.guestRelayConfigured && !wishes.lanActive && (!wishes.offline || wishes.publicActive); onClicked: controller.setPublicGuestServer(!wishes.publicActive) }
    Text {
        Layout.fillWidth: true
        text: wishes.lanActive ? "客人用同一局域网扫码。" : wishes.offline ? "离线演出期间可用局域网扫码。" :
              wishes.stateData.guestRelayStatus || (!wishes.stateData.guestRelayConfigured ? "配置公网祝福服务后可开启手机流量扫码。" : "选择一种方式开启扫码。")
        color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap
    }
    Text {
        objectName: "wishPublicCloseStatus"
        Layout.fillWidth: true
        visible: !!text
        text: wishes.stateData.guestRelayCloseStatus || ""
        color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap
    }
    Text { Layout.fillWidth: true; text: "你审核后才上屏，每条显示 15 秒。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
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
