import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: timeline
    objectName: "studioTimeline"
    property var stateData: ({})
    property int selectedSegment: 0
    property string selectedObject: "lyrics"
    property string selectedItemId: ""
    property real zoom: 1
    property var rowDefinitions: []
    property string rowSignature: ""
    readonly property var composition: stateData.composition || ({})
    readonly property real duration: Math.max(1, Number(stateData.durationMs || 0))
    readonly property real trackWidth: Math.max(180, viewport.width-75)*zoom
    signal seekRequested(real position)
    signal segmentSelected(int index)
    signal objectSelected(string kind)
    signal itemSelected(string kind,string id)
    signal clipMoveRequested(string id,real startMs)
    signal clipRangeRequested(string id,real startMs,real durationMs,real sourceInMs)
    signal analyzeRequested()
    signal markerRequested(real position)
    radius: 2; color: Theme.surface; border.color: Theme.stroke
    function clock(ms) { if(duration<10000) return (Math.max(0,Number(ms))/1000).toFixed(1)+"s"; var secs=Math.floor(Math.max(0,Number(ms))/1000); return Math.floor(secs/60).toString().padStart(2,"0")+":"+(secs%60).toString().padStart(2,"0"); }
    function seekRelative(px) { if (!stateData.busy && stateData.audioPath) seekRequested(Math.max(0,Math.min(duration,px/trackWidth*duration))); }
    function snap(ms) { var frame=1000/Number((stateData.settings || {}).fps || 24); return Math.round(ms/frame)*frame; }
    function rebuild() {
        var value=stateData.composition || {};
        var clips=value.clips || [], texts=value.texts || [], audio=value.audioClips || [], lyrics=stateData.segments || [];
        var signature=JSON.stringify([clips,texts,audio,lyrics,stateData.audioTitle]);
        if(signature===rowSignature) return;
        rowSignature=signature;
        var rows=[{kind:"clip",label:"画面",rows:clips},{kind:"lyrics",label:"歌词",rows:lyrics}];
        rows.push({kind:"audio",label:"主音频",rows:[]});
        texts.forEach(function(text,index){rows.push({kind:"text",label:"文字 "+(index+1),rows:[text]});});
        audio.forEach(function(clip,index){rows.push({kind:"audioClip",label:"附音 "+(index+1),rows:[clip]});});
        rowDefinitions=rows;
    }
    onStateDataChanged: rebuild()
    onCompositionChanged: rebuild()
    Component.onCompleted: rebuild()
    ColumnLayout {
        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; anchors.topMargin: 8; anchors.bottomMargin: 7; spacing: 3
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 28; spacing: 3
            Text { text: "时间轴"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
            Text { text: (timeline.composition.clips || []).length+" 镜 · "+(timeline.stateData.segments || []).length+" 句"; color: Theme.muted; font.pixelSize: 10 }
            Item { Layout.fillWidth: true }
            StudioButton { objectName: "studioAnalyzeAudio"; text: "分析波形"; iconName: "audio"; implicitHeight: 26; enabled: !!timeline.stateData.audioPath && !timeline.stateData.busy; onClicked: timeline.analyzeRequested() }
            StudioButton { objectName: "studioAddMarker"; text: "添加标记"; iconName: "check"; implicitHeight: 26; enabled: !!timeline.stateData.audioPath && !timeline.stateData.busy; onClicked: timeline.markerRequested(Number(timeline.stateData.positionMs || 0)) }
            StudioButton { text: "缩小时间轴"; iconName: "zoomOut"; iconOnly: true; implicitHeight: 26; implicitWidth: 26; enabled: timeline.zoom>1; onClicked: timeline.zoom=Math.max(1,timeline.zoom/1.5) }
            Text { text: Math.round(timeline.zoom*100)+"%"; color: Theme.secondary; font.family: Theme.numberFont; font.pixelSize: 10; Layout.preferredWidth: 34; horizontalAlignment: Text.AlignHCenter }
            StudioButton { text: "放大时间轴"; iconName: "zoomIn"; iconOnly: true; implicitHeight: 26; implicitWidth: 26; enabled: timeline.zoom<8 && !!timeline.stateData.audioPath; onClicked: timeline.zoom=Math.min(8,timeline.zoom*1.5) }
        }
        Text { visible: !timeline.stateData.audioPath; Layout.fillWidth: true; Layout.fillHeight: true; text: "导入歌曲后，在这里安排歌词与画面。"; color: Theme.muted; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
        Flickable {
            id: viewport
            visible: !!timeline.stateData.audioPath
            objectName: "studioTimelineViewport"
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: 75+timeline.trackWidth
            contentHeight: 30+timeline.rowDefinitions.length*35
            boundsBehavior: Flickable.StopAtBounds; clip: true
            ScrollBar.horizontal: ScrollBar { policy: timeline.zoom>1 ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded; height: 5 }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 4 }
            Item {
                id: tracks
                width: viewport.contentWidth; height: viewport.contentHeight
                Item {
                    x: 75; width: timeline.trackWidth; height: 27
                    Repeater { model: 9; delegate: Item { required property int index; x: index/8*(timeline.trackWidth-1); width: 1; height: tracks.height; Rectangle { width: 1; height: parent.height; color: "#282f39" } Text { x: parent.index===8 ? -implicitWidth-3 : 3; y: 1; text: timeline.clock(parent.index/8*timeline.duration); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 9 } } }
                    Repeater { model: timeline.stateData.beatsMs || []; delegate: Rectangle { required property var modelData; x: Number(modelData)/timeline.duration*timeline.trackWidth; y: 17; width: 1; height: 7; color: "#658477" } }
                    MouseArea {
                        objectName: "studioTimelineScrub"
                        anchors.fill: parent; enabled: !timeline.stateData.busy; cursorShape: Qt.PointingHandCursor
                        preventStealing: true
                        onPressed: function(mouse) { timeline.seekRelative(mouse.x); }
                        onPositionChanged: function(mouse) { if (pressed) timeline.seekRelative(mouse.x); }
                    }
                    Repeater {
                        model: timeline.composition.markers || []
                        delegate: Button {
                            required property var modelData
                            objectName: "studioMarker-"+modelData.id
                            x: Number(modelData.positionMs)/timeline.duration*timeline.trackWidth-4; y: 17; width: 9; height: 10
                            text: modelData.label || "标记"; hoverEnabled: true; activeFocusOnTab: true; Accessible.name: text
                            ToolTip.visible: hovered; ToolTip.text: text
                            onClicked: timeline.seekRequested(modelData.positionMs)
                            contentItem: Item {}
                            background: Rectangle { color: Theme.amber; radius: 2; border.color: parent.activeFocus ? Theme.text : Theme.amber }
                        }
                    }
                }
                Repeater {
                    model: timeline.rowDefinitions
                    delegate: Item {
                        id: lane
                        required property var modelData
                        required property int index
                        x: 75; y: 29+index*35; width: timeline.trackWidth; height: 31
                        Rectangle { anchors.fill: parent; radius: 5; color: lane.modelData.kind==="audio" && timeline.stateData.audioPath ? "#223730" : Theme.canvas }
                        MouseArea { anchors.fill: parent; enabled: !timeline.stateData.busy; onClicked: function(mouse) { timeline.objectSelected(lane.modelData.kind); timeline.seekRelative(mouse.x); } }
                        Canvas {
                            id: waveform
                            anchors.fill: parent
                            visible: lane.modelData.kind==="audio"
                            onWidthChanged: requestPaint()
                            Connections { target: timeline; function onStateDataChanged() { waveform.requestPaint(); } }
                            onPaint: {
                                var context=getContext("2d"), buckets=timeline.stateData.waveform || [];
                                context.clearRect(0,0,width,height);
                                context.strokeStyle="#648d7b"; context.lineWidth=1;
                                for(var i=0;i<buckets.length;i++) {
                                    var bucket=buckets[i], low=Array.isArray(bucket) ? bucket[0] : bucket.min, high=Array.isArray(bucket) ? bucket[1] : bucket.max;
                                    if(low===undefined || high===undefined) continue;
                                    var px=i/Math.max(1,buckets.length-1)*width;
                                    context.beginPath(); context.moveTo(px,height/2+Number(low)*height*0.44); context.lineTo(px,height/2+Number(high)*height*0.44); context.stroke();
                                }
                            }
                        }
                        Repeater {
                            model: lane.modelData.rows
                            delegate: Button {
                                id: timelineClip
                                required property var modelData
                                required property int index
                                objectName: "timeline-"+lane.modelData.kind+"-"+(modelData.id || index)
                                property bool dragging: false
                                property bool resizing: false
                                property real draftStartMs: Number(modelData.startMs || 0)
                                property real draftDurationMs: Number(modelData.durationMs || 0)
                                property real draftSourceInMs: Number(modelData.sourceInMs || 0)
                                function selectClip() { if(lane.modelData.kind==="lyrics") timeline.segmentSelected(index); else timeline.itemSelected(lane.modelData.kind,modelData.id); timeline.seekRequested(Number(modelData.startMs || 0)); }
                                property bool selected: lane.modelData.kind==="lyrics" ? timeline.selectedObject==="lyrics" && timeline.selectedSegment===index : timeline.selectedObject===lane.modelData.kind && timeline.selectedItemId===modelData.id
                                x: Number(dragging || resizing ? draftStartMs : modelData.startMs || 0)/timeline.duration*timeline.trackWidth
                                width: Math.max(6,Number(resizing ? draftDurationMs : lane.modelData.kind==="lyrics" ? modelData.endMs-modelData.startMs : modelData.durationMs || 0)/timeline.duration*timeline.trackWidth-2)
                                height: 31; leftPadding: 6; rightPadding: 6
                                text: lane.modelData.kind==="lyrics" || lane.modelData.kind==="text" ? modelData.text || "文字" : lane.modelData.kind==="audioClip" ? "附加音轨" : modelData.kind==="color" ? "纯色" : String((((timeline.composition.sources || {})[modelData.sourceId] || {}).identity || {}).path || "分镜").split('/').pop()
                                enabled: !timeline.stateData.busy; hoverEnabled: true; activeFocusOnTab: true
                                Accessible.name: text+(modelData.locked ? "，已锁定" : "")
                                ToolTip.visible: hovered; ToolTip.text: text
                                onClicked: selectClip()
                                background: Rectangle { radius: 5; color: timelineClip.selected ? "#48627d" : lane.modelData.kind==="clip" ? "#44354d" : lane.modelData.kind==="text" ? "#36515c" : lane.modelData.kind==="audioClip" ? "#2a483b" : "#293b4e"; border.color: timelineClip.activeFocus || timelineClip.selected ? Theme.accent : "#4c5264" }
                                contentItem: Text { text: (timelineClip.modelData.locked ? "▣ " : "")+timelineClip.text; color: Theme.text; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                                MouseArea {
                                    id: moveArea
                                    objectName: "clipMoveArea-"+timelineClip.modelData.id
                                    preventStealing: true
                                    anchors.fill: parent
                                    enabled: lane.modelData.kind==="clip" && !timelineClip.modelData.locked && !timeline.stateData.busy
                                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                                    property real pressX: 0
                                    property real originalStart: 0
                                    onPressed: function(mouse) { pressX=mapToItem(lane,mouse.x,mouse.y).x; originalStart=Number(timelineClip.modelData.startMs || 0); timelineClip.draftStartMs=originalStart; }
                                    onPositionChanged: function(mouse) {
                                        if(!pressed) return;
                                        var delta=mapToItem(lane,mouse.x,mouse.y).x-pressX;
                                        if(Math.abs(delta)>3) timelineClip.dragging=true;
                                        if(timelineClip.dragging) timelineClip.draftStartMs=Math.max(0,Math.min(timeline.duration-Number(timelineClip.modelData.durationMs || 0),timeline.snap(originalStart+delta/timeline.trackWidth*timeline.duration)));
                                    }
                                    onReleased: {
                                        var changed=timelineClip.dragging;
                                        var start=timelineClip.draftStartMs;
                                        timelineClip.dragging=false;
                                        if(changed) timeline.clipMoveRequested(timelineClip.modelData.id,start);
                                        else timelineClip.selectClip();
                                    }
                                    onCanceled: timelineClip.dragging=false
                                }
                                Repeater {
                                    model: lane.modelData.kind==="clip" ? ["left","right"] : []
                                    delegate: Rectangle {
                                        id: trimHandle
                                        required property string modelData
                                        width: 7; height: timelineClip.height-4; y: 2
                                        x: modelData==="left" ? 1 : timelineClip.width-width-1
                                        visible: timelineClip.width>24 && !timelineClip.modelData.locked
                                        color: "#60758a"; opacity: trimMouse.containsMouse || trimMouse.pressed ? 1 : 0.38; radius: 2
                                        MouseArea {
                                            id: trimMouse
                                            preventStealing: true
                                            anchors.fill: parent; hoverEnabled: true
                                            enabled: !timeline.stateData.busy && !timelineClip.modelData.locked
                                            cursorShape: Qt.SizeHorCursor
                                            property real pressX: 0
                                            property real originalStart: 0
                                            property real originalDuration: 0
                                            property real originalIn: 0
                                            onPressed: function(mouse) {
                                                pressX=mapToItem(lane,mouse.x,mouse.y).x;
                                                originalStart=Number(timelineClip.modelData.startMs || 0); originalDuration=Number(timelineClip.modelData.durationMs || 0); originalIn=Number(timelineClip.modelData.sourceInMs || 0);
                                                timelineClip.draftStartMs=originalStart; timelineClip.draftDurationMs=originalDuration; timelineClip.draftSourceInMs=originalIn; timelineClip.resizing=true;
                                            }
                                            onPositionChanged: function(mouse) {
                                                if(!pressed) return;
                                                var delta=mapToItem(lane,mouse.x,mouse.y).x-pressX;
                                                var target=timeline.snap(delta/timeline.trackWidth*timeline.duration);
                                                var frame=1000/Number((timeline.stateData.settings || {}).fps || 24);
                                                if(trimHandle.modelData==="left") {
                                                    timelineClip.draftStartMs=Math.max(0,Math.min(originalStart+originalDuration-frame,timeline.snap(originalStart+target)));
                                                    timelineClip.draftDurationMs=originalStart+originalDuration-timelineClip.draftStartMs;
                                                    timelineClip.draftSourceInMs=timelineClip.modelData.kind==="video" ? originalIn+(timelineClip.draftStartMs-originalStart)*Number(timelineClip.modelData.rate || 1) : originalIn;
                                                } else timelineClip.draftDurationMs=Math.max(frame,Math.min(timeline.duration-originalStart,timeline.snap(originalDuration+target)));
                                            }
                                            onReleased: { var start=timelineClip.draftStartMs, duration=timelineClip.draftDurationMs, sourceIn=timelineClip.draftSourceInMs; timelineClip.resizing=false; timeline.clipRangeRequested(timelineClip.modelData.id,start,duration,sourceIn); }
                                            onCanceled: timelineClip.resizing=false
                                        }
                                    }
                                }
                                Rectangle { visible: lane.modelData.kind==="clip" && (timelineClip.modelData.transition || {}).id!=="cut" && Number((timelineClip.modelData.transition || {}).durationMs || 0)>0; x: 0; width: Math.min(parent.width,Number((timelineClip.modelData.transition || {}).durationMs || 0)/timeline.duration*timeline.trackWidth); height: parent.height; color: "#44677d"; opacity: 0.45 }
                            }
                        }
                        Text { visible: !(lane.modelData.rows || []).length; anchors.left: parent.left; anchors.leftMargin: 8; anchors.verticalCenter: parent.verticalCenter; text: lane.modelData.kind==="audio" ? (timeline.stateData.waveform || []).length ? "" : timeline.stateData.audioTitle || "导入主歌曲" : lane.modelData.kind==="clip" ? "添加素材分镜或自动分镜" : lane.modelData.kind==="text" ? "添加独立文字" : "导入字幕"; color: Theme.muted; font.pixelSize: 10 }
                    }
                }
                Rectangle {
                    x: 75+Math.min(timeline.trackWidth-1,Math.max(0,Number(timeline.stateData.positionMs || 0)/timeline.duration*timeline.trackWidth)); y: 23; width: 1; height: tracks.height-23; color: Theme.text
                    Rectangle { x: -3; y: -3; width: 7; height: 7; radius: 2; color: Theme.text }
                }
                Rectangle { x: viewport.contentX; width: 70; height: tracks.height; color: Theme.surface; z: 3 }
                Repeater {
                    model: timeline.rowDefinitions
                    delegate: StudioButton {
                        required property var modelData
                        required property int index
                        x: viewport.contentX; y: 29+index*35; width: 65; height: 31; z: 4
                        text: modelData.label; padding: 3; font.pixelSize: 10
                        selected: timeline.selectedObject===modelData.kind
                        onClicked: { if((modelData.rows || []).length && modelData.kind!=="lyrics") timeline.itemSelected(modelData.kind,modelData.rows[0].id); else timeline.objectSelected(modelData.kind); }
                    }
                }
            }
        }
    }
}
