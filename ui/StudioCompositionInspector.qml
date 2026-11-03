import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: inspector
    objectName: "studioCompositionInspector"
    property var studioModel: null
    property var stateData: ({})
    property string kind: "clip"
    property string itemId: ""
    readonly property var composition: stateData.composition || ({})
    readonly property var itemData: (kind==="clip" ? composition.clips || [] : kind==="text" ? composition.texts || [] : composition.audioClips || []).filter(function(row) { return row.id===itemId; })[0] || ({})
    readonly property var sourceData: (composition.sources || {})[itemData.sourceId] || ({})
    property string committedText: ""
    onItemDataChanged: { var next=String(itemData.text || ""); if(next!==committedText) independentText.text=next; committedText=next; }
    onItemIdChanged: { independentText.text=String(itemData.text || ""); committedText=String(itemData.text || ""); }
    readonly property bool canEdit: !!studioModel && !stateData.busy && !!itemData.id
    readonly property bool unlocked: canEdit && !itemData.locked
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    function edit(key, value) {
        if (!canEdit) return;
        if (kind==="clip") studioModel.editClip(itemId,key,value);
        else if (kind==="text") studioModel.editText(itemId,key,value);
        else studioModel.editAudioClip(itemId,key,value);
    }
    ColumnLayout {
        width: inspector.availableWidth
        spacing: 10
        Text { Layout.fillWidth: true; text: inspector.itemData.id ? inspector.kind==="text" ? "独立文字" : inspector.kind==="audioClip" ? "附加音轨" : inspector.itemData.kind==="color" ? "纯色分镜" : String((inspector.sourceData.identity || {}).path || "素材分镜").split('/').pop() : "在时间轴选择一个片段"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideMiddle }
        RowLayout {
            visible: !!inspector.itemData.id; Layout.fillWidth: true
            Text { text: "锁定片段"; color: Theme.secondary; font.pixelSize: 11 }
            Item { Layout.fillWidth: true }
            Switch { objectName: "compositionLocked"; checked: !!inspector.itemData.locked; enabled: inspector.canEdit; Accessible.name: "锁定选中片段"; onToggled: inspector.edit("locked",checked) }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            StudioValueField { Layout.fillWidth: true; label: "时间位置"; unit: "秒"; editContext: inspector.itemId; fieldName: "compositionStart"; value: Number(inspector.itemData.startMs || 0)/1000; maximum: Number(inspector.stateData.durationMs || 0)/1000; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("startMs",value*1000); } }
            StudioValueField { Layout.fillWidth: true; label: "时长"; unit: "秒"; editContext: inspector.itemId; fieldName: "compositionDuration"; value: Number(inspector.itemData.durationMs || 0)/1000; minimum: 0.001; maximum: Number(inspector.stateData.durationMs || 0)/1000; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("durationMs",value*1000); } }
        }
        RowLayout {
            visible: inspector.kind!=="text" && inspector.itemData.kind!=="color"; Layout.fillWidth: true; spacing: 8
            StudioValueField { Layout.fillWidth: true; label: "源素材入点"; unit: "秒"; editContext: inspector.itemId; fieldName: "compositionSourceIn"; value: Number(inspector.itemData.sourceInMs || 0)/1000; maximum: Math.max(0,Number(inspector.sourceData.durationMs || 0))/1000; enabled: inspector.unlocked && inspector.itemData.kind!=="image"; onCommitted: function(value) { inspector.edit("sourceInMs",value*1000); } }
            StudioValueField { Layout.fillWidth: true; label: "播放速度"; unit: "倍"; editContext: inspector.itemId; fieldName: "compositionRate"; value: Number(inspector.itemData.rate || 1); minimum: 0.25; maximum: 4; decimals: 2; enabled: inspector.unlocked && inspector.itemData.kind!=="image"; onCommitted: function(value) { inspector.edit("rate",value); } }
        }
        ColumnLayout {
            visible: inspector.kind==="clip"; Layout.fillWidth: true; spacing: 10
            TextField { visible: inspector.itemData.kind==="color"; objectName: "compositionClipColor"; Layout.fillWidth: true; implicitHeight: 31; text: inspector.itemData.color || "#101216"; color: Theme.text; selectByMouse: true; enabled: inspector.unlocked; Accessible.name: "分镜背景颜色"; validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ } onEditingFinished: if(acceptableInput) inspector.edit("color",text); background: Rectangle { radius: 8; color: Theme.canvas; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
            Text { text: "画面适配"; color: Theme.secondary; font.pixelSize: 10 }
            DarkComboBox { objectName: "compositionFit"; Layout.fillWidth: true; implicitHeight: 31; model: ["铺满并裁切", "完整显示"]; currentIndex: inspector.itemData.fit==="contain" ? 1 : 0; enabled: inspector.unlocked; Accessible.name: "分镜画面适配"; onActivated: function(index) { inspector.edit("fit",index===0 ? "cover" : "contain") } }
            StudioCatalogPicker { Layout.fillWidth: true; label: "片头转场"; controlName: "compositionTransition"; rows: inspector.stateData.transitions || []; selectedId: (inspector.itemData.transition || {}).id || "cut"; enabled: inspector.unlocked; onChosen: function(id) { inspector.studioModel.setTransition(inspector.itemId,id,id==="cut" ? 0 : Number((inspector.itemData.transition || {}).durationMs || 300)); } }
            StudioValueField { Layout.fillWidth: true; label: "转场时长"; unit: "秒"; editContext: inspector.itemId; fieldName: "compositionTransitionDuration"; value: Number((inspector.itemData.transition || {}).durationMs || 0)/1000; maximum: Number(inspector.itemData.durationMs || 0)/2000; enabled: inspector.unlocked && (inspector.itemData.transition || {}).id!=="cut"; onCommitted: function(value) { inspector.studioModel.setTransition(inspector.itemId,(inspector.itemData.transition || {}).id || "cut",value*1000); } }
            Text { Layout.fillWidth: true; text: "转场混合相邻分镜边界，素材入点与速度决定实际采样画面。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
            RowLayout {
                Layout.fillWidth: true
                StudioButton { Layout.fillWidth: true; objectName: "compositionSplit"; text: "在此拆分"; enabled: inspector.unlocked && Number(inspector.stateData.positionMs || 0)>Number(inspector.itemData.startMs || 0) && Number(inspector.stateData.positionMs || 0)<Number(inspector.itemData.startMs || 0)+Number(inspector.itemData.durationMs || 0); onClicked: inspector.studioModel.splitClip(inspector.itemId,Number(inspector.stateData.positionMs || 0)) }
                StudioButton { objectName: "compositionDelete"; text: "删除"; enabled: inspector.unlocked; onClicked: inspector.studioModel.removeClip(inspector.itemId) }
            }
        }
        ColumnLayout {
            visible: inspector.kind==="text"; Layout.fillWidth: true; spacing: 10
            TextArea {
                id: independentText
                objectName: "compositionTextEditor"
                Layout.fillWidth: true; Layout.preferredHeight: 86
                text: inspector.itemData.text || ""
                color: Theme.text; font.family: "PingFang SC"; font.pixelSize: 13
                selectByMouse: true; wrapMode: TextEdit.Wrap; enabled: inspector.unlocked
                background: Rectangle { radius: 9; color: Theme.canvas; border.color: independentText.activeFocus ? Theme.focus : "#39424d" }
            }
            StudioButton { objectName: "compositionTextApply"; Layout.fillWidth: true; text: "应用文字"; enabled: inspector.unlocked && independentText.text!==(inspector.itemData.text || ""); onClicked: inspector.edit("text",independentText.text) }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                StudioValueField { Layout.fillWidth: true; label: "水平位置"; unit: "%"; editContext: inspector.itemId; fieldName: "compositionTextX"; value: Number(inspector.itemData.x || 0)*100; maximum: 100; decimals: 1; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("x",value/100); } }
                StudioValueField { Layout.fillWidth: true; label: "垂直位置"; unit: "%"; editContext: inspector.itemId; fieldName: "compositionTextY"; value: Number(inspector.itemData.y || 0)*100; maximum: 100; decimals: 1; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("y",value/100); } }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                StudioValueField { Layout.fillWidth: true; label: "字号"; editContext: inspector.itemId; fieldName: "compositionTextSize"; value: Number(inspector.itemData.fontSize || 42); minimum: 8; maximum: 200; decimals: 0; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("fontSize",value); } }
                StudioValueField { Layout.fillWidth: true; label: "不透明度"; unit: "%"; editContext: inspector.itemId; fieldName: "compositionTextOpacity"; value: Number(inspector.itemData.opacity || 0)*100; maximum: 100; decimals: 0; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("opacity",value/100); } }
            }
            DarkComboBox { Layout.fillWidth: true; implicitHeight: 31; model: ["苹方", "黑体", "宋体"]; currentIndex: Math.max(0,["PingFang SC","Heiti SC","Songti SC"].indexOf(inspector.itemData.fontFamily || "PingFang SC")); enabled: inspector.unlocked; Accessible.name: "独立文字字体"; onActivated: function(index) { inspector.edit("fontFamily",["PingFang SC","Heiti SC","Songti SC"][index]) } }
            DarkCheckBox { text: "加粗"; checked: !!inspector.itemData.fontBold; enabled: inspector.unlocked; onToggled: inspector.edit("fontBold",checked) }
            TextField { objectName: "compositionTextColor"; Layout.fillWidth: true; implicitHeight: 31; text: inspector.itemData.color || "#F1F3F6"; color: Theme.text; selectByMouse: true; enabled: inspector.unlocked; Accessible.name: "独立文字颜色"; validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ } onEditingFinished: if(acceptableInput) inspector.edit("color",text); background: Rectangle { radius: 8; color: Theme.canvas; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
            StudioCatalogPicker { Layout.fillWidth: true; label: "文字布局"; controlName: "compositionTextLayout"; rows: inspector.stateData.textLayouts || []; selectedId: inspector.itemData.layoutId || "standard"; enabled: inspector.unlocked; onChosen: function(id) { inspector.edit("layoutId",id); } }
            StudioCatalogPicker { Layout.fillWidth: true; label: "文字动画"; controlName: "compositionTextAnimation"; rows: inspector.stateData.effects || []; selectedId: inspector.itemData.animationId || "none"; enabled: inspector.unlocked; onChosen: function(id) { inspector.edit("animationId",id); } }
            RowLayout { Layout.fillWidth: true; StudioButton { objectName: "compositionTextPNG"; Layout.fillWidth: true; text: "导出 PNG"; enabled: inspector.canEdit; onClicked: inspector.studioModel.exportTextPNG(inspector.itemId) } StudioButton { text: "删除"; enabled: inspector.unlocked; onClicked: inspector.studioModel.removeText(inspector.itemId) } }
        }
        ColumnLayout {
            visible: inspector.kind==="audioClip"; Layout.fillWidth: true; spacing: 10
            StudioValueField { Layout.fillWidth: true; label: "音量增益"; unit: "%"; editContext: inspector.itemId; fieldName: "compositionAudioGain"; value: Number(inspector.itemData.gain || 0)*100; maximum: 200; decimals: 0; enabled: inspector.unlocked; onCommitted: function(value) { inspector.edit("gain",value/100); } }
            Text { Layout.fillWidth: true; text: "附加音轨与主歌曲共同混合，100% 为原素材音量。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
            StudioButton { objectName: "compositionAudioDelete"; Layout.fillWidth: true; text: "移除音轨"; enabled: inspector.unlocked; onClicked: inspector.studioModel.removeAudioClip(inspector.itemId) }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
