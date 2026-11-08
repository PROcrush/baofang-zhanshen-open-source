import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "deckCard-" + deckId
    property string deckId: "A"
    property var deck: ({})
    property bool active: false
    property bool replay: true
    property bool observedOnly: false
    // 原版 AX：每盘的实际文件确认状态（来自 controller.state.rekordboxAX.bindings）
    property var binding: ({})
    readonly property bool confirmed: !!binding.versionId
    readonly property bool dense: height < 150
    readonly property string cacheState: deck.cacheState || (deck.valid ? (deck.quality === "暂无歌词" || deck.quality === "unavailable" ? "missing" : "ready") : "missing")
    readonly property string cacheText: deck.cacheLabel || (cacheState === "ready" ? "缓存就绪" : cacheState === "preparing" ? "准备中" : cacheState === "suspended" ? "已挂起" : cacheState === "error" ? "准备失败" : "待准备")
    signal loadRequested()
    signal preparationRequested()
    signal confirmRequested()
    color: Theme.surface
    border.color: active ? Theme.hairlineStrong : Theme.hairline
    border.width: 1
    radius: Theme.radiusCard
    implicitHeight: 180
    AccentBar {
        visible: card.active; vertical: true
        anchors.left: parent.left; anchors.leftMargin: 1; anchors.verticalCenter: parent.verticalCenter
        width: 2; height: Math.min(40, parent.height - 24)
    }
    ColumnLayout {
        objectName: "deckContents-" + card.deckId
        anchors.fill: parent; anchors.margins: card.dense ? 9 : 14; anchors.leftMargin: card.dense ? 11 : 16; spacing: card.dense ? 4 : 7
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            PillFrame {
                Layout.preferredWidth: card.dense ? 24 : 28; Layout.preferredHeight: Layout.preferredWidth
                accentRing: card.active; fill: card.active ? Theme.accentSoft : Theme.wash
                Text { anchors.centerIn: parent; text: card.deckId; color: card.active ? Theme.text : Theme.secondary; font.family: Theme.numberFont; font.pixelSize: card.dense ? 12 : 13; font.weight: Font.Medium }
            }
            Text { text: card.deckId + " 盘"; color: Theme.secondary; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            Text { text: card.active ? "当前歌词来源" : "待命"; color: card.active ? Theme.accent : Theme.muted; font.pixelSize: 11; font.weight: card.active ? Font.Medium : Font.Normal }
        }
        Text {
            Layout.fillWidth: true
            text: !card.deck.valid && !card.replay ? "等待 DJ 曲目" : (card.deck.title || "等待载入曲目")
            color: Theme.text; font.pixelSize: card.dense ? 13 : 15; font.weight: Font.Medium
            elide: Text.ElideRight; maximumLineCount: card.dense ? 1 : 2; wrapMode: card.dense ? Text.NoWrap : Text.Wrap
            ToolTip.visible: titleHover.containsMouse && !!card.deck.title
            ToolTip.text: card.deck.title || ""
            MouseArea { id: titleHover; anchors.fill: parent; acceptedButtons: Qt.NoButton; hoverEnabled: true }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: card.deck.positionText || "--:--.---"; color: Theme.text; font.family: Theme.numberFont; font.pixelSize: card.dense ? 11 : 13 }
            Item { Layout.fillWidth: true }
            Text { objectName:"deckPlaybackState-"+card.deckId; text: !card.deck.valid ? "等待播放状态" : card.observedOnly ? "位置直读" : card.deck.playing ? "播放中" : "已暂停"; color: !card.deck.valid ? Theme.amber : card.observedOnly ? Theme.secondary : card.deck.playing ? Theme.green : Theme.secondary; font.pixelSize: 11 }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 3; radius: 1; color: Theme.stroke
            visible: Number(card.deck.durationMs || 0)>0
            Rectangle { height:parent.height; radius:1; width:parent.width*Math.max(0,Math.min(1,Number(card.deck.positionMs || 0)/Math.max(1,Number(card.deck.durationMs || 0)))); color:card.active ? Theme.accent : Theme.secondary }
        }
        RowLayout {
            visible: card.observedOnly
            Layout.fillWidth: true; spacing: 6
            Rectangle { width: 6; height: 6; radius: 3; color: card.confirmed ? Theme.green : Theme.amber }
            Text {
                objectName: "deckAXBinding-" + card.deckId
                Layout.fillWidth: true
                text: card.confirmed ? "已确认文件" : (card.binding.status || "待确认当前歌曲")
                color: card.confirmed ? Theme.green : Theme.amber; font.pixelSize: 11; elide: Text.ElideRight
                ToolTip.visible: bindingHover.containsMouse && !!card.binding.path
                ToolTip.text: card.binding.path || ""
                MouseArea { id: bindingHover; anchors.fill: parent; acceptedButtons: Qt.NoButton; hoverEnabled: true }
            }
            StudioButton {
                objectName: "bindRekordboxAX-" + card.deckId
                text: card.confirmed ? "重新确认" : "确认 " + card.deckId + " 盘歌曲"
                selected: !card.confirmed
                implicitHeight: card.dense ? 25 : 28; font.pixelSize: 11
                hint: "手动核对 " + card.deckId + " 盘实际音频文件"
                onClicked: card.confirmRequested()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            StatusPill {
                objectName: "deckCache-" + card.deckId
                implicitHeight: card.dense ? 22 : 24
                text: !card.deck.valid && !card.replay ? "等待曲目状态" : card.cacheState === "ready" ? (card.deck.lyricSource ? card.cacheText : card.deck.needsReview ? "歌词待复核" : "歌词已就绪") : card.cacheState === "preparing" ? "正在核验歌曲" : card.cacheState === "missing" ? "尚无歌词" : card.cacheState === "error" ? "歌曲核验失败" : card.cacheState === "suspended" ? "准备已挂起" : "等待核验"
                tone: card.cacheState === "ready" && !card.deck.needsReview ? "green" : card.cacheState === "error" ? "red" : "amber"
                ToolTip.visible: cacheHover.containsMouse
                ToolTip.text: card.deck.bindingMessage || card.deck.errorText || card.cacheText
                MouseArea { id: cacheHover; anchors.fill: parent; acceptedButtons: Qt.NoButton; hoverEnabled: true }
            }
            Item { Layout.fillWidth: true }
            StudioButton { visible: card.replay; text: "演示此盘"; implicitWidth: 78; implicitHeight: card.dense ? 25 : 28; font.pixelSize: 11; onClicked: card.loadRequested() }
            StudioButton { objectName:"deckPrepare-"+card.deckId; visible:!card.replay && (card.cacheState==="missing" || (card.cacheState==="ready" && !!card.deck.needsReview)) && !!card.deck.path; text:card.deck.needsReview ? "复核歌词" : "准备歌词"; implicitHeight:28; font.pixelSize:11; onClicked:card.preparationRequested() }
        }
    }
}
