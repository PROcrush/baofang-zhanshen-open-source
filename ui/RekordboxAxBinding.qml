import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: panel
    objectName: "rekordboxAXBindingPopup"
    property var axState: ({})
    property string deckId: "A"
    property string selectedVersionId: ""
    readonly property var observation: (axState.observed || {})[deckId] || ({})
    readonly property var binding: (axState.bindings || {})[deckId] || ({})
    readonly property var candidates: axState.candidates || []
    readonly property int candidateIndex: {
        for (var i=0;i<candidates.length;i++) if(candidates[i].versionId===selectedVersionId)return i;
        return -1;
    }
    readonly property var candidate: candidateIndex>=0 ? candidates[candidateIndex] : ({})
    readonly property var candidateLabels: candidates.map(function(row){return row.title || row.path;})
    height: 420
    padding: 20
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color:Theme.elevated; radius:10; border.color:Theme.stroke }

    function openFor(side) {
        deckId=side;
        var current=(axState.bindings || {})[side] || ({});
        selectedVersionId=current.versionId || "";
        open();
    }

    ColumnLayout {
        anchors.fill:parent; spacing:12
        RowLayout {
            Layout.fillWidth:true
            Text { text:"确认 " + panel.deckId + " 盘歌曲"; color:Theme.text; font.pixelSize:18; font.weight:Font.Medium }
            Item { Layout.fillWidth:true }
            StudioButton { objectName:"rekordboxAXBindingClose"; text:"关闭"; implicitHeight:30; onClicked:panel.close() }
        }
        Text {
            objectName:"rekordboxAXObservedTitle"; Layout.fillWidth:true
            text:"rekordbox 当前曲目：" + (panel.observation.title || "尚未读到曲目")
            color:Theme.text; font.pixelSize:13; wrapMode:Text.Wrap
            Accessible.role:Accessible.StaticText; Accessible.name:text
        }
        Text {
            Layout.fillWidth:true; text:"界面源位置：" + (panel.observation.available ? Number(panel.observation.positionSeconds || 0).toFixed(1) + " 秒" : "尚未读取")
            color:Theme.secondary; font.pixelSize:11
        }
        Text {
            Layout.fillWidth:true; text:"确认当前盘实际加载的是该文件；换歌后重新确认。"; color:Theme.amber; font.pixelSize:12; wrapMode:Text.Wrap
        }
        DarkComboBox {
            objectName:"rekordboxAXCandidateCombo"; Layout.fillWidth:true; implicitHeight:36
            model:panel.candidateLabels; currentIndex:panel.candidateIndex
            displayText:currentIndex>=0 ? panel.candidateLabels[currentIndex] : panel.candidates.length ? "选择已准备的实际歌曲文件" : "尚无已准备的歌曲文件"
            enabled:panel.candidates.length>0
            onActivated:function(index){panel.selectedVersionId=panel.candidates[index].versionId;}
        }
        ScrollView {
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true
            ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
            TextArea {
                objectName:"rekordboxAXCandidatePath"; readOnly:true; selectByMouse:true; wrapMode:TextEdit.WrapAnywhere
                text:panel.candidate.path || "请选择文件并核对完整路径。已有同名歌曲时，也要确认具体 Remix 和剪辑版本。"
                color:panel.candidate.path ? Theme.text : Theme.secondary; font.pixelSize:12
                background:Rectangle { color:Theme.surface; radius:6 }
                Accessible.name:"实际歌曲文件路径，" + text
            }
        }
        Text {
            objectName:"rekordboxAXBindingStatus"; Layout.fillWidth:true; color:Theme.secondary; font.pixelSize:11; wrapMode:Text.Wrap
            text:panel.binding.versionId ? "当前已确认：" + (panel.binding.title || panel.binding.path || "已选择文件") : panel.binding.status || "此盘尚未确认歌曲；确认前不显示该盘歌词。"
        }
        RowLayout {
            Layout.fillWidth:true; spacing:8
            StudioButton {
                objectName:"rekordboxAXBindingClear"; text:"清除歌曲确认"; enabled:!!panel.binding.versionId || panel.binding.status==="正在核验所选文件"
                onClicked:controller.clearRekordboxAXBinding(panel.deckId)
            }
            Item { Layout.fillWidth:true }
            StudioButton {
                objectName:"rekordboxAXBindingConfirm"; text:"确认 " + panel.deckId + " 盘歌曲"; tone:"primary"
                enabled:!!panel.observation.available && !!panel.candidate.versionId
                onClicked:{controller.bindRekordboxAX(panel.deckId,panel.candidate.versionId);panel.close();}
            }
        }
    }
}
