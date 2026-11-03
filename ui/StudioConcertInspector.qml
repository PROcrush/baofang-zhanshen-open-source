import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: concert
    property var studioModel: null
    property var stateData: ({})
    property int selectedRow: 0
    readonly property var rows: stateData.concertRows || []
    readonly property var row: rows[selectedRow] || ({})
    readonly property var coverage: stateData.concertCoverage || ({})
    readonly property bool editable: !!studioModel && !stateData.busy && !!stateData.concertHasDraft
    contentWidth: availableWidth; clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    function review(key,value) { if(editable && rows.length) studioModel.reviewConcertRow(selectedRow,key,value); }
    ColumnLayout {
        width: concert.availableWidth; spacing: 10
        Text { text: concert.rows.length ? "候选 "+(concert.selectedRow+1)+" / "+concert.rows.length : "演唱会对齐"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
        Text { visible: concert.rows.length>0 && !concert.stateData.concertHasDraft; Layout.fillWidth: true; text: "编排已应用，后续在时间轴中修改分镜。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
        Text { Layout.fillWidth: true; text: concert.row.reason || "导入演唱会视频及其来源字幕，再生成候选分镜。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.4 }
        Text { visible: !!concert.row.id; text: concert.row.purpose==="paired" ? "歌词匹配候选，需核对画面" : concert.row.purpose==="cutaway" ? "补镜素材，需核对衔接" : "未匹配段，使用补镜或纯色"; color: concert.row.reviewed ? Theme.green : Theme.amber; font.pixelSize: 10 }
        RowLayout {
            Layout.fillWidth: true; visible: !!concert.row.id; spacing: 8
            StudioValueField { Layout.fillWidth: true; label: "目标位置"; unit: "秒"; editContext: String(concert.selectedRow); fieldName: "concertStart"; value: Number(concert.row.startMs || 0)/1000; maximum: Number(concert.stateData.durationMs || 0)/1000; enabled: concert.editable; onCommitted: function(value) { concert.review("startMs",value*1000); } }
            StudioValueField { Layout.fillWidth: true; label: "时长"; unit: "秒"; editContext: String(concert.selectedRow); fieldName: "concertDuration"; value: Number(concert.row.durationMs || 0)/1000; minimum: 0.001; maximum: Number(concert.stateData.durationMs || 0)/1000; enabled: concert.editable; onCommitted: function(value) { concert.review("durationMs",value*1000); } }
        }
        RowLayout {
            Layout.fillWidth: true; visible: !!concert.row.id; spacing: 8
            StudioValueField { Layout.fillWidth: true; label: "源视频入点"; unit: "秒"; editContext: String(concert.selectedRow); fieldName: "concertSourceIn"; value: Number(concert.row.sourceInMs || 0)/1000; enabled: concert.editable && concert.row.kind==="video"; onCommitted: function(value) { concert.review("sourceInMs",value*1000); } }
            StudioValueField { Layout.fillWidth: true; label: "速度"; unit: "倍"; editContext: String(concert.selectedRow); fieldName: "concertRate"; value: Number(concert.row.rate || 1); minimum: 0.25; maximum: 4; decimals: 2; enabled: concert.editable && concert.row.kind==="video"; onCommitted: function(value) { concert.review("rate",value); } }
        }
        StudioCatalogPicker {
            Layout.fillWidth: true; label: "该候选使用的素材"; controlName: "concertRowSource"
            rows: [{id:"",label:"纯色补镜"}].concat((concert.stateData.materialSources || []).filter(function(s){return s.kind!=="audio";}).map(function(s){return {id:s.sourceId || s.id,label:s.title || s.label || String((s.identity || {}).path || s.path || "素材").split('/').pop()};}))
            selectedId: concert.row.sourceId || ""; enabled: concert.editable && !!concert.row.id
            onChosen: function(id) { concert.review("sourceId",id); }
        }
        DarkCheckBox { objectName: "concertRowReviewed"; visible: !!concert.row.id; text: "我已核对这一段"; checked: !!concert.row.reviewed; enabled: concert.editable; onToggled: concert.review("reviewed",checked) }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
        Text { text: "覆盖 "+Math.round(Number(concert.coverage.coverageRatio || 0)*100)+"% · "+(concert.coverage.fullCoverage ? "完整" : "有空档"); color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
        Text { Layout.fillWidth: true; text: "匹配 "+(Number(concert.coverage.pairedMs || 0)/1000).toFixed(1)+"s · 补镜 "+(Number(concert.coverage.cutawayMs || 0)/1000).toFixed(1)+"s\n待复核 "+Number(concert.coverage.reviewRequiredRows || 0)+" 段"; color: Theme.secondary; font.pixelSize: 10; lineHeight: 1.5; wrapMode: Text.Wrap }
        DarkCheckBox { objectName: "concertAllReviewed"; text: "我已核对全部候选"; checked: !!concert.stateData.concertReviewed; enabled: concert.editable && concert.rows.length>0; onToggled: concert.studioModel.setConcertReviewed(checked) }
        StudioButton { objectName: "concertApplyPlan"; Layout.fillWidth: true; text: "应用已复核分镜"; tone: "primary"; enabled: concert.editable && concert.rows.length>0 && !!concert.coverage.fullCoverage && (concert.stateData.concertReviewed || concert.rows.every(function(row){return !!row.reviewed;})); onClicked: concert.studioModel.applyConcertPlan() }
        Text { Layout.fillWidth: true; text: "候选来自字幕匹配。人工确认表示已复核分镜，不代表歌唱同步已经验收。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
        Item { Layout.preferredHeight: 8 }
    }
}
