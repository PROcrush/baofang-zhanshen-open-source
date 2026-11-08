import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtMultimedia

Item {
    id: panel
    objectName: "studioWorkspace"
    property var studioModel: typeof studio !== "undefined" ? studio : null
    readonly property var studioState: studioModel ? studioModel.state : ({})
    readonly property var settings: studioState.settings || ({})
    readonly property var segments: studioState.segments || []
    readonly property var templates: studioState.templates || []
    readonly property var effects: studioState.effects || []
    readonly property real duration: Math.max(1, Number(studioState.durationMs || 0))
    readonly property bool compact: width < 1080
    readonly property bool hasAudio: !!studioState.audioPath
    readonly property bool editable: !!studioModel && !studioState.busy
    readonly property bool canRender: editable && !!studioState.engineReady && !!studioState.audioPath && (segments.length>0 || (composition.clips || []).length>0 || (composition.texts || []).length>0)
    readonly property bool hasError: !!studioState.error || /失败|无效|不可用|超出/.test(String(studioState.status || ""))
    readonly property var selectedLine: segments[selectedSegment] || ({})
    readonly property bool hasFrameProvider: typeof studioState.previewRevision === "number"
    property int libraryPage: 0
    property int inspectorPage: 2
    property int selectedSegment: 0
    property string selectedObject: "lyrics"
    property string selectedItemId: ""
    property string selectedSourceId: ""
    property int selectedConcertRow: 0
    readonly property var composition: studioState.composition || ({})
    readonly property var selectedSource: (studioState.materialSources || []).filter(function(source){return (source.sourceId || source.id)===selectedSourceId;})[0] || ({})
    property string editorCommittedText: ""
    property string editorSourcePath: ""
    property string observedResultUrl: ""
    property bool showRendered: false
    property bool advancedEditing: false
    readonly property bool hasLyricDraft: advancedEditing && segments.length>0 && lyricsEdit.text!==String(selectedLine.text || "")
    property string guidedAudioPath: ""
    property int previousSegmentCount: 0
    readonly property var renderedPlayer: mediaLoader.item ? mediaLoader.item.player : null
    readonly property bool renderedPlaying: !!renderedPlayer && renderedPlayer.playbackState === MediaPlayer.PlayingState
    readonly property real renderedPosition: renderedPlayer ? renderedPlayer.position : 0
    readonly property var upcomingLine: {
        for (var i=0;i<segments.length;i++) if (Number(segments[i].startMs)>Number(studioState.positionMs || 0)) return segments[i];
        return null;
    }
    signal toolsRequested()
    signal liveRequested()
    signal apiRequested(string kind)

    function selectedIndex(rows, id) { for (var i=0; i<rows.length; i++) if (rows[i].id === id) return i; return 0; }
    function timeLabel(ms) {
        var value = Math.floor(Math.max(0, Number(ms || 0)));
        return Math.floor(value/60000).toString().padStart(2,"0") + ":" + Math.floor(value/1000%60).toString().padStart(2,"0") + "." + (value%1000).toString().padStart(3,"0");
    }
    function fileName(path) { return String(path || "").split(/[\\/]/).pop(); }
    function commitPendingLyrics() {
        if(!advancedEditing) return simpleWorkspace.commitEditing();
        return !segments.length || lyricsEdit.text===String(selectedLine.text || "") || studioModel.updateSegment(selectedSegment,lyricsEdit.text);
    }
    function saveProgress(asCopy) {
        if(commitPendingLyrics()) asCopy ? studioModel.saveProjectAs() : studioModel.saveProject();
    }
    function readableStatus() {
        var message=String(studioState.status || "");
        if(studioState.busy && /CPU|渲染|生成完整影片/.test(message)) return "正在生成视频，请稍候。";
        if(studioState.recoveryPath && /恢复副本/.test(message)) return "上一工程的未保存修改已保留，可点击右侧恢复。";
        if(/源|素材/.test(message) && /变化/.test(message)) return "素材文件已经变化，请重新导入后再继续。";
        return message;
    }
    function syncEditor(force) {
        var line = segments[selectedSegment] || ({});
        var committed = String(line.text || "");
        var source = String(studioState.audioPath || "");
        if (force || committed !== editorCommittedText || source !== editorSourcePath) lyricsEdit.text = committed;
        editorCommittedText = committed;
        editorSourcePath = source;
    }
    function selectLine(index, seekToLine) {
        selectedSegment = Math.max(0, Math.min(Number(index), Math.max(0, segments.length-1)));
        selectedObject = "lyrics"; libraryPage = 1; if(inspectorPage===2) inspectorPage=0;
        if (seekToLine && editable && segments.length) studioModel.seek(Number((segments[selectedSegment] || {}).startMs || 0));
    }
    function selectObject(kind) {
        selectedObject = kind;
        libraryPage = kind === "lyrics" ? 1 : kind === "concert" ? 2 : 0;
        var rows = kind === "clip" ? composition.clips || [] : kind === "text" ? composition.texts || [] : kind === "audioClip" ? composition.audioClips || [] : [];
        if(rows.length && !rows.some(function(row){return row.id===selectedItemId;})) selectedItemId=rows[rows.length-1].id;
    }
    function selectNewest(kind) {
        selectObject(kind);
        var current=studioModel ? studioModel.state.composition || {} : composition;
        var rows=kind==="clip" ? current.clips || [] : kind==="text" ? current.texts || [] : current.audioClips || [];
        if(rows.length) selectedItemId=rows[rows.length-1].id;
    }
    function navigateLine(delta) { if (segments.length) selectLine(selectedSegment+delta, true); }
    onSelectedSegmentChanged: {
        var bounded = Math.max(0, Math.min(selectedSegment, Math.max(0, segments.length-1)));
        if (bounded !== selectedSegment) selectedSegment = bounded;
        syncEditor(true);
    }
    onSelectedLineChanged: syncEditor(false)
    onSegmentsChanged: {
        selectedSegment = Math.max(0, Math.min(selectedSegment, Math.max(0, segments.length-1)));
        syncEditor(false);
    }
    onStudioStateChanged: {
        var audio=String(studioState.audioPath || "");
        var newRows=studioState.segments || [];
        if(audio!==guidedAudioPath) { guidedAudioPath=audio; selectedObject="lyrics"; libraryPage=0; inspectorPage=2; selectedSegment=0; previousSegmentCount=0; }
        if(previousSegmentCount===0 && newRows.length>0) {
            libraryPage=1; inspectorPage=2;
            if(Number(studioState.positionMs || 0)===0) Qt.callLater(function() {
                var fresh=panel.studioModel.state,rows=fresh.segments || [];
                if(!fresh.busy && String(fresh.audioPath || "")===audio && rows.length && Number(fresh.positionMs || 0)===0)
                    panel.studioModel.seek(Number(rows[0].startMs)+Math.min(300,(Number(rows[0].endMs)-Number(rows[0].startMs))/2));
            });
        }
        previousSegmentCount=newRows.length;
        var url = String(studioState.resultUrl || "");
        if (url && url !== observedResultUrl) showRendered = true;
        if (!url) showRendered = false;
        observedResultUrl = url;
        var all = (composition.clips || []).concat(composition.texts || [],composition.audioClips || []);
        if (selectedItemId && !all.some(function(row){return row.id===selectedItemId;})) selectedItemId="";
        selectedConcertRow=Math.max(0,Math.min(selectedConcertRow,Math.max(0,(studioState.concertRows || []).length-1)));
    }
    onVisibleChanged: {
        if (!visible) {
            if (renderedPlayer) renderedPlayer.pause();
            if (studioModel) studioModel.pausePreview();
        }
    }
    onShowRenderedChanged: {
        if (studioModel) studioModel.pausePreview();
        if (!showRendered && renderedPlayer) renderedPlayer.pause();
    }
    Component.onCompleted: syncEditor(true)
    Connections { target: panel.studioModel; function onStateChanged() { Qt.callLater(function() { panel.syncEditor(false); }); } }
    Shortcut { sequences: [StandardKey.Save]; enabled: panel.visible && panel.editable && !!panel.studioState.audioPath; onActivated: panel.saveProgress(false) }
    Shortcut { sequences: [StandardKey.SaveAs]; enabled: panel.visible && panel.editable && panel.hasAudio; onActivated: panel.saveProgress(true) }
    Shortcut { sequences: [StandardKey.Open]; enabled: panel.visible && panel.editable; onActivated: if(panel.commitPendingLyrics()) panel.studioModel.openProject() }
    Shortcut { sequences: [StandardKey.Undo]; enabled: panel.visible && panel.editable && !!panel.studioState.canUndo && (!panel.Window.window || !panel.Window.window.activeFocusItem || typeof panel.Window.window.activeFocusItem.cursorPosition === "undefined"); onActivated: panel.studioModel.undo() }
    Shortcut { sequences: [StandardKey.Redo]; enabled: panel.visible && panel.editable && !!panel.studioState.canRedo && (!panel.Window.window || !panel.Window.window.activeFocusItem || typeof panel.Window.window.activeFocusItem.cursorPosition === "undefined"); onActivated: panel.studioModel.redo() }
    Shortcut {
        sequence: "Space"
        enabled: panel.visible && panel.advancedEditing && panel.editable && panel.hasAudio && (!panel.Window.window || !panel.Window.window.activeFocusItem || typeof panel.Window.window.activeFocusItem.cursorPosition === "undefined")
        onActivated: {
            if (panel.showRendered && panel.renderedPlayer) panel.renderedPlaying ? panel.renderedPlayer.pause() : panel.renderedPlayer.play();
            else panel.studioModel.togglePreview();
        }
    }

    component StudioMenu: Menu {
        id: popupMenu
        padding: 6
        background: Rectangle { color: Theme.elevated; radius: 14; border.color: "#49515d" }
        delegate: MenuItem {
            id: menuControl
            implicitHeight: 35
            leftPadding: 11; rightPadding: 11
            contentItem: Text { text: menuControl.text; font.family: "PingFang SC"; font.pixelSize: 12; color: menuControl.enabled ? Theme.text : Theme.muted; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 8; color: menuControl.highlighted ? "#3a4656" : "transparent" }
        }
    }
    StudioMenu {
        id: projectMenu
        MenuItem { text: "打开工程…"; enabled: panel.editable; onTriggered: if(panel.commitPendingLyrics()) panel.studioModel.openProject() }
        MenuItem { text: "保存工程"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered: panel.saveProgress(false) }
        MenuItem { text: "工程另存为…"; enabled: panel.editable && panel.hasAudio; onTriggered: panel.saveProgress(true) }
        MenuSeparator { visible: (panel.studioState.recentProjects || []).length>0 }
        Repeater {
            model: panel.studioState.recentProjects || []
            delegate: MenuItem {
                required property var modelData
                text: "最近：" + modelData.title + (modelData.available ? "" : "（文件不可用）")
                enabled: panel.editable && modelData.available
                onTriggered: if(panel.commitPendingLyrics()) panel.studioModel.openProject(modelData.path)
            }
        }
    }
    StudioMenu {
        id: importMenu
        MenuItem { text: "歌曲音频…"; enabled: panel.editable; onTriggered: if(panel.commitPendingLyrics()) { panel.selectedObject = "audio"; panel.libraryPage = 0; panel.studioModel.chooseAudio(); } }
        MenuItem { text: "歌词字幕…"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered: { panel.selectedObject = "lyrics"; panel.libraryPage = 1; panel.studioModel.chooseSubtitles(); } }
        MenuItem { text: "云端识别歌词…"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered:panel.apiRequested("speech") }
        MenuItem { text: "工程素材…"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered: { panel.libraryPage=0; panel.studioModel.chooseMaterial(); } }
        MenuItem { text: "独立文字"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered: { panel.studioModel.addText(); panel.selectNewest("text"); } }
        MenuItem { text: "背景图片 / 视频…"; enabled: panel.editable && !!panel.studioState.audioPath; onTriggered: { panel.selectedObject = "background"; panel.libraryPage = 0; panel.studioModel.chooseBackground(); } }
    }
    StudioMenu {
        id: exportMenu
        MenuItem { text: "480p · 854 × 480"; enabled: panel.editable; onTriggered: panel.studioModel.setSetting("width", 854) }
        MenuItem { text: "720p · 1280 × 720"; enabled: panel.editable; onTriggered: panel.studioModel.setSetting("width", 1280) }
        MenuItem { text: "1080p · 1920 × 1080"; enabled: panel.editable; onTriggered: panel.studioModel.setSetting("width", 1920) }
    }

    StudioSimplePanel {
        id: simpleWorkspace
        anchors.fill: parent; visible: !panel.advancedEditing
        studioModel: panel.studioModel
        onAdvancedRequested: { panel.studioModel.pausePreview(); panel.advancedEditing=true; }
        onLiveRequested: panel.liveRequested()
        onApiRequested: function(kind) { panel.apiRequested(kind); }
    }
    ColumnLayout {
        visible: panel.advancedEditing
        anchors.fill: parent
        anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.bottomMargin: 8
        spacing: 0
        RowLayout {
            visible: !panel.hasAudio
            Layout.fillWidth: true; Layout.preferredHeight: 38
            StudioButton { objectName: "studioEmptyReturnSimple"; text: "返回简洁界面"; onClicked: panel.advancedEditing=false }
            Item { Layout.fillWidth: true }
        }
        Rectangle {
            visible: panel.hasAudio
            Layout.fillWidth: true; Layout.preferredHeight: 42
            radius: 0
            color: Theme.surface
            border.color: Theme.hairline
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 7; anchors.rightMargin: 7; spacing: 2
                StudioButton { id: projectButton; objectName: "studioProjectMenu"; text: "工程"; iconName: "project"; hint: "打开、另存为或继续最近的工程"; enabled: panel.editable; onClicked: projectMenu.popup(projectButton, 0, height+7) }
                StudioButton { objectName: "studioReturnSimple"; text: "返回简洁界面"; enabled: panel.editable; onClicked: if(panel.commitPendingLyrics()) panel.advancedEditing=false }
                StudioButton { id: importButton; text: "更多导入"; iconName: "import"; enabled: panel.editable; onClicked: importMenu.popup(importButton, 0, height+7) }
                Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 18; Layout.leftMargin: 5; Layout.rightMargin: 5; color: "#46505c" }
                StudioButton { text: "撤销"; iconName: "undo"; iconOnly: true; hint: "撤销上次修改"; enabled: panel.editable && !!panel.studioState.canUndo; onClicked: panel.studioModel.undo() }
                StudioButton { text: "重做"; iconName: "redo"; iconOnly: true; enabled: panel.editable && !!panel.studioState.canRedo; onClicked: panel.studioModel.redo() }
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; spacing: 6
                    Text { Layout.fillWidth: true; text: panel.studioState.projectPath ? panel.fileName(panel.studioState.projectPath).replace(/\.bfstudio\.json$/, "") : panel.studioState.audioTitle ? panel.studioState.audioTitle.replace(/\.[^.]+$/, "") : "新建歌词视频"; color: Theme.text; font.pixelSize: 12; elide: Text.ElideMiddle; horizontalAlignment: Text.AlignHCenter }
                    Text { objectName:"studioSaveStatus"; text:panel.studioState.dirty || panel.hasLyricDraft ? "未保存" : panel.studioState.projectPath ? "已保存" : "新工程"; color:panel.studioState.dirty || panel.hasLyricDraft ? Theme.amber : Theme.muted; font.pixelSize:11 }
                }
                StudioButton { text: "工具"; iconName: "tools"; enabled: panel.editable; onClicked: panel.toolsRequested() }
                StudioButton { objectName: "studioSaveProject"; text: "保存"; iconName: "save"; hint: "保存当前工程，⌘S；另存为在工程菜单中"; enabled: panel.editable && !!panel.studioState.audioPath; onClicked: panel.saveProgress(false) }
                StudioButton { objectName: "studioOutputSettings"; text: "导出设置"; iconName: "tools"; enabled: panel.editable; onClicked: panel.selectObject("export") }
                StudioButton { text: "导出视频"; iconName: "export"; tone: "primary"; enabled: panel.canRender; onClicked: panel.selectObject("export") }
            }
        }
        StudioWorkflow {
            visible: panel.hasAudio
            Layout.fillWidth: true; stateData: panel.studioState
            hasPicture: !!panel.studioState.backgroundPath || (panel.composition.clips || []).length>0
            currentStep: panel.selectedObject==="export" ? 3 : panel.selectedObject==="audio" ? 0 : panel.selectedObject==="lyrics" && (!panel.segments.length || panel.inspectorPage!==2) ? 1 : 2
            onRequested: function(step) {
                if(step===0 && panel.commitPendingLyrics()) { panel.libraryPage=0; panel.studioModel.chooseAudio(); }
                else if(step===1) { panel.selectObject("lyrics"); panel.inspectorPage=0; }
                else if(step===2) { panel.selectedObject="lyrics"; panel.inspectorPage=2; panel.libraryPage=0; }
                else panel.selectObject("export");
            }
        }
        RowLayout {
            visible: !!panel.studioState.busy || panel.hasError || !!panel.studioState.recoveryPath
            Layout.fillWidth: true; Layout.preferredHeight: visible ? 28 : 0; spacing: 7
            Rectangle { width: 5; height: 5; radius: 3; color: panel.hasError ? Theme.red : panel.studioState.busy ? Theme.accent : Theme.muted }
            Text {
                id: taskStatus
                Layout.fillWidth: true
                text: panel.readableStatus() || "从选择歌曲开始。"
                color: panel.hasError ? Theme.red : Theme.secondary
                font.pixelSize: 11; elide: Text.ElideRight
                ToolTip.visible: statusHover.containsMouse; ToolTip.text: panel.studioState.status || text
                MouseArea { id: statusHover; anchors.fill: parent; acceptedButtons: Qt.NoButton; hoverEnabled: true }
            }
            ProgressBar { visible: !!panel.studioState.busy; Layout.preferredWidth: 85; Layout.preferredHeight: 4; from: 0; to: 1; value: Number(panel.studioState.progress || 0); indeterminate: value <= 0 }
            Text { visible: !!panel.studioState.busy && Number(panel.studioState.progress || 0)>0; text: Math.round(Number(panel.studioState.progress || 0)*100)+"%"; color: Theme.accent; font.family: Theme.numberFont; font.pixelSize: 10 }
            StudioButton { visible: !!panel.studioState.busy; text: "取消任务"; implicitHeight: 26; onClicked: panel.studioModel.cancelJob() }
            StudioButton { visible: !!panel.studioState.recoveryPath; text: "恢复未保存工程"; implicitHeight: 26; enabled: panel.editable; onClicked: if(panel.commitPendingLyrics()) panel.studioModel.openProject(panel.studioState.recoveryPath) }
            Text { text: panel.studioState.dirty ? "未保存" : panel.studioState.projectPath ? "已保存" : "新工程"; color: panel.studioState.dirty ? Theme.amber : Theme.muted; font.pixelSize: 10 }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 255
            radius: 0; color: panel.hasAudio ? Theme.surface : Theme.canvas; border.color: panel.hasAudio ? Theme.stroke : "transparent"
            RowLayout {
                anchors.fill: parent; spacing: 0
                StudioLibraryPane {
                    id: libraryPane
                    objectName: "studioLibrary"
                    visible: panel.hasAudio
                    Layout.preferredWidth: panel.compact ? 236 : 270; Layout.minimumWidth: 236; Layout.maximumWidth: panel.compact ? 236 : 270; Layout.fillHeight: true
                    studioModel: panel.studioModel; stateData: panel.studioState; commitEdits: panel.commitPendingLyrics
                    selectedPage: panel.libraryPage; selectedSegment: panel.selectedSegment; selectedObject: panel.selectedObject
                    selectedSourceId: panel.selectedSourceId; selectedConcertRow: panel.selectedConcertRow
                    onCloudRecognitionRequested: panel.apiRequested("speech")
                    onPageSelected: function(index) { panel.libraryPage=index; if(index===2) panel.selectObject("concert"); }
                    onObjectSelected: function(kind) { if(kind==="clip" || kind==="text") panel.selectNewest(kind); else panel.selectObject(kind); }
                    onLineSelected: function(index) { panel.selectLine(index,true); }
                    onSourceSelected: function(id) { panel.selectedSourceId=id; panel.selectedObject="source"; }
                    onConcertSelected: function(index) { panel.selectedConcertRow=index; panel.selectObject("concert"); panel.showRendered=false; if(panel.editable) panel.studioModel.seek(Number((panel.studioState.concertRows || [])[index].startMs || 0)); }
                }
                Rectangle { visible: panel.hasAudio; Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.stroke }
                Item {
                    objectName: "studioPreviewFrame"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 238
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: panel.compact ? 12 : 18; spacing: 10
                        RowLayout {
                            visible: panel.hasAudio
                            Layout.fillWidth: true; spacing: 4
                            Text { text: "播放器"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
                            Item { Layout.fillWidth: true }
                            StudioButton { objectName:"studioEffectsButton"; text:"字幕特效"; iconName:"style"; implicitHeight:28; enabled:panel.segments.length>0; hint:panel.segments.length ? "选择字幕样式和文字动画" : "添加歌词后选择字幕特效"; onClicked: { panel.selectedObject="lyrics"; panel.inspectorPage=2; panel.showRendered=false; templateInspector.openEffects(); } }
                            StudioButton { objectName: "studioPreviewBackground"; text: "添加背景"; iconName: "background"; implicitHeight: 28; enabled: panel.editable && !!panel.studioState.audioPath; visible: !!panel.studioState.audioPath; onClicked: panel.studioModel.chooseBackground() }
                            StudioButton { text: "编辑"; padding: 8; implicitHeight: 26; implicitWidth: 47; selected: !panel.showRendered; onClicked: panel.showRendered = false }
                            StudioButton { text: "成片"; padding: 8; implicitHeight: 26; implicitWidth: 47; selected: panel.showRendered; enabled: !!panel.studioState.resultUrl; onClicked: panel.showRendered = true }
                        }
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Rectangle {
                                id: canvas
                                anchors.centerIn: parent
                                width: parent.width; height: parent.height
                                radius: 2; color: Theme.canvas; clip: true
                                Image {
                                    id: editorFrame
                                    objectName: "studioLiveFrame"
                                    anchors.fill: parent
                                    visible: panel.advancedEditing && !panel.showRendered && panel.hasFrameProvider && !!panel.studioState.audioPath && (panel.segments.length>0 || (panel.composition.clips || []).length>0 || (panel.composition.texts || []).length>0)
                                    cache: false
                                    asynchronous: true
                                    retainWhileLoading: true
                                    sourceSize: Qt.size(Math.min(960, Number(panel.settings.width || 854)), Math.min(960, Number(panel.settings.width || 854))*Number(panel.settings.height || 480)/Number(panel.settings.width || 854))
                                    source: visible ? "image://studioPreview/frame?revision=" + String(panel.studioState.previewRevision) + "&position=" + Number(panel.studioState.positionMs || 0) + "&settings=" + encodeURIComponent(JSON.stringify(panel.settings)) + "&text=" + encodeURIComponent(panel.studioState.currentText || "") + (panel.selectedObject==="concert" && (panel.studioState.concertRows || []).length ? "&concertRow="+panel.selectedConcertRow : "") : ""
                                    fillMode: Image.PreserveAspectFit
                                }
                                Image { anchors.fill: parent; visible: !panel.showRendered && !panel.hasFrameProvider && !!panel.studioState.backgroundIsImage; source: visible ? panel.studioState.backgroundUrl || "" : ""; fillMode: Image.PreserveAspectCrop }
                                StudioWelcome {
                                    anchors.fill: parent; studioModel: panel.studioModel; stateData: panel.studioState
                                    visible: !panel.showRendered && (!panel.studioState.audioPath || (!panel.segments.length && !(panel.composition.clips || []).length && !(panel.composition.texts || []).length))
                                    onSubtitlesRequested: { panel.selectObject("lyrics"); panel.studioModel.chooseSubtitles(); }
                                    onLiveRequested: panel.liveRequested()
                                    onApiRequested:panel.apiRequested("speech")
                                }
                                Loader {
                                    id: mediaLoader
                                    anchors.fill: parent
                                    active: panel.visible && panel.advancedEditing && panel.showRendered && !!panel.studioState.resultUrl
                                    visible: panel.showRendered
                                    sourceComponent: Item {
                                        property alias player: moviePlayer
                                        VideoOutput { id: videoOutput; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectFit }
                                        MediaPlayer {
                                            id: moviePlayer
                                            objectName: "studioRenderedPlayer"
                                            property bool priming: false
                                            source: panel.showRendered ? panel.studioState.resultUrl || "" : ""
                                            videoOutput: videoOutput
                                            audioOutput: AudioOutput { id: movieAudio; volume: 0.7; muted: moviePlayer.priming }
                                            onSourceChanged: playbackError.text = ""
                                            onMediaStatusChanged: {
                                                if (mediaStatus === MediaPlayer.LoadedMedia && source.toString().length>0) {
                                                    priming = true;
                                                    position = Math.min(duration, Number(panel.studioState.positionMs || 0));
                                                    play();
                                                }
                                            }
                                            onErrorOccurred: function(error, errorString) { playbackError.text = errorString; }
                                        }
                                        Connections {
                                            target: videoOutput.videoSink
                                            function onVideoFrameChanged() {
                                                if (moviePlayer.priming) {
                                                    moviePlayer.pause();
                                                    moviePlayer.priming = false;
                                                }
                                            }
                                        }
                                    }
                                }
                                Text { id: playbackError; visible: panel.showRendered && text.length>0; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10; color: Theme.red; font.pixelSize: 11; wrapMode: Text.Wrap }
                                Text { text: panel.studioState.playbackError || ""; visible: !panel.showRendered && text.length>0; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10; color: Theme.red; font.pixelSize: 11; wrapMode: Text.Wrap }
                            }
                        }
                        RowLayout {
                            visible: panel.hasAudio
                            Layout.fillWidth: true; spacing: 4
                            StudioButton { text: "上一句"; iconName: "previous"; iconOnly: true; implicitHeight: 29; implicitWidth: 29; visible: !panel.showRendered; enabled: panel.editable && panel.segments.length>0 && panel.selectedSegment>0; onClicked: panel.navigateLine(-1) }
                            StudioButton { objectName: "studioPlayPreview"; text: panel.studioState.playbackPlaying ? "暂停" : "播放"; iconName: panel.studioState.playbackPlaying ? "pause" : "play"; hint: "实时播放歌曲和画面，空格播放 / 暂停"; implicitHeight: 29; implicitWidth: 82; visible: !panel.showRendered; enabled: panel.editable && panel.hasAudio; onClicked: panel.studioModel.togglePreview() }
                            StudioButton { text: panel.renderedPlaying ? "暂停渲染预览" : "播放渲染预览"; iconName: panel.renderedPlaying ? "pause" : "play"; iconOnly: true; implicitHeight: 29; implicitWidth: 29; visible: panel.showRendered; enabled: !!panel.renderedPlayer; onClicked: panel.renderedPlaying ? panel.renderedPlayer.pause() : panel.renderedPlayer.play() }
                            Text { text: panel.timeLabel(panel.showRendered ? panel.renderedPosition : panel.studioState.positionMs).slice(0,8); color: Theme.text; font.family: Theme.numberFont; font.pixelSize: 11 }
                            Text { text: "/ " + panel.timeLabel(panel.studioState.durationMs).slice(0,8); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10 }
                            StudioButton { text: "下一句"; iconName: "next"; iconOnly: true; implicitHeight: 29; implicitWidth: 29; visible: !panel.showRendered; enabled: panel.editable && panel.selectedSegment<panel.segments.length-1; onClicked: panel.navigateLine(1) }
                            StudioButton { objectName: "studioPreviewMute"; text: panel.studioState.playbackMuted ? "恢复声音" : "静音"; implicitHeight: 29; implicitWidth: 84; visible: !panel.showRendered; enabled: panel.hasAudio; onClicked: panel.studioModel.togglePreviewMute() }
                            Item { Layout.fillWidth: true }
                            StudioButton { text: panel.showRendered ? "查看成片" : "生成预览"; iconName: "film"; iconOnly: panel.compact; hint: panel.showRendered ? "打开已生成的视频文件" : "生成带声音的实际视频预览"; implicitHeight: 29; implicitWidth: panel.compact ? 29 : 112; enabled: panel.showRendered ? !!panel.studioState.resultPath : panel.canRender; onClicked: { if(panel.commitPendingLyrics()) panel.showRendered ? panel.studioModel.openResult() : panel.studioModel.renderPreview(); } }
                        }
                        RowLayout {
                            visible: panel.hasAudio && !panel.showRendered && panel.segments.length>0 && !panel.studioState.currentText
                            Layout.fillWidth: true; spacing: 8
                            Text {
                                objectName: "studioLyricGapHint"
                                Layout.fillWidth: true
                                text: panel.upcomingLine ? "前奏 / 间奏 · 下一句 " + panel.timeLabel(panel.upcomingLine.startMs).slice(0,8) : "歌曲尾奏 · 歌词已结束"
                                color: Theme.secondary; font.pixelSize: 11; elide: Text.ElideRight
                            }
                            StudioButton {
                                objectName: "studioJumpToLyrics"
                                text: "跳到下一句"; implicitWidth: 98; implicitHeight: 27
                                visible: !!panel.upcomingLine; enabled: panel.editable
                                onClicked: panel.studioModel.seek(Number(panel.upcomingLine.startMs)+Math.min(300,(Number(panel.upcomingLine.endMs)-Number(panel.upcomingLine.startMs))/2))
                            }
                        }
                        RowLayout {
                            visible: panel.hasAudio
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: panel.showRendered ? panel.studioState.resultFormat==="mov-alpha" ? "透明字幕预览" : "已生成完整预览，可播放检查" : (panel.composition.audioClips || []).length ? "实时预览主歌曲 · 附加音轨请生成预览检查" : panel.selectedObject==="concert" ? "实时预览候选画面 · 等待人工复核" : "实时预览 · 空格播放 / 暂停，拖动时间轴定位"; color: Theme.muted; font.pixelSize: 10; elide: Text.ElideRight }
                            StudioButton { id: sizeButton; text: Number(panel.settings.height || 480)+"p"; padding: 7; hint: "导出分辨率"; implicitHeight: 23; implicitWidth: 52; enabled: panel.editable; onClicked: exportMenu.popup(sizeButton, 0, height+5) }
                        }
                    }
                }
                Rectangle { visible: panel.hasAudio && studioInspector.visible; Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.stroke }
                Item {
                    id: studioInspector
                    objectName: "studioInspector"
                    visible: !!panel.studioState.audioPath && (panel.segments.length>0 || panel.selectedObject!=="lyrics")
                    Layout.preferredWidth: panel.compact ? 268 : 318; Layout.minimumWidth: panel.compact ? 268 : 318; Layout.maximumWidth: panel.compact ? 268 : 318; Layout.fillHeight: true
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true; Layout.preferredHeight: 26
                            StudioIcon { Layout.preferredWidth: 17; Layout.preferredHeight: 17; name: panel.selectedObject==="lyrics" ? "lyrics" : panel.selectedObject==="audio" || panel.selectedObject==="audioClip" ? "audio" : "tools"; ink: Theme.secondary }
                            Text { Layout.fillWidth: true; text: ({lyrics:"歌词与样式",audio:"主音频",background:"背景属性",clip:"分镜属性",text:"文字属性",audioClip:"音轨属性",source:"素材属性",concert:"候选审阅",export:"预览与导出"})[panel.selectedObject] || "片段属性"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
                            Text { visible: panel.selectedObject==="lyrics" && panel.segments.length>0; text: (panel.selectedSegment+1)+" / "+panel.segments.length; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10 }
                        }
                        StackLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            currentIndex: panel.selectedObject==="lyrics" ? 0 : panel.selectedObject==="audio" ? 1 : panel.selectedObject==="background" ? 2 : panel.selectedObject==="source" ? 4 : panel.selectedObject==="concert" ? 5 : panel.selectedObject==="export" ? 6 : 3
                            ColumnLayout {
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    StudioButton {
                                        objectName: "studioKaraokeMode"
                                        Layout.fillWidth: true; implicitWidth: 100; implicitHeight: 31
                                        text: "传统 KTV"; selected: panel.settings.templateId === "karaoke"
                                        hint: "黄白扫色、粗描边大字、上下两行轮唱，保留实际歌曲时码"
                                        enabled: panel.editable
                                        onClicked: panel.studioModel.applyKaraokeStyle()
                                    }
                                    StudioButton {
                                        objectName: "studioKaraokeCardMode"
                                        Layout.fillWidth: true; implicitWidth: 100; implicitHeight: 31
                                        text: "简洁双行"; selected: panel.settings.templateId === "karaoke-card"
                                        hint: "当前句和下一句，带简洁底板"
                                        enabled: panel.editable
                                        onClicked: panel.studioModel.applyKaraokeStyle("karaoke-card")
                                    }
                                }
                                StudioButton {
                                    objectName:"studioLyricSweep"; text:"歌词走带"; Layout.fillWidth:true; checkable:true
                                    checked:!!panel.settings.lyricSweep; selected:checked; enabled:panel.editable
                                    hint:"所有字幕模板开启或关闭跟唱扫色 · Ctrl+Alt+K"
                                    onClicked:panel.studioModel.setSetting("lyricSweep",checked)
                                    Accessible.onToggleAction:panel.studioModel.setSetting("lyricSweep",!panel.settings.lyricSweep)
                                }
                                Text {
                                    objectName: "studioKaraokeTiming"
                                    Layout.fillWidth: true
                                    visible: !!panel.settings.lyricSweep
                                    text: {
                                        var info=panel.studioState.karaokeTiming || {}, count=Number(info.timed || 0), total=panel.segments.length;
                                        if(!count) return "按句估算扫色；导入增强 LRC / 逐字 JSON 可更贴合人声。";
                                        return count+" / "+total+" 句有字词时码 · 长音继续，停顿保持"+(Number(info.sentence || 0)>0 ? "；其余按句估算扫色" : "");
                                    }
                                    color: Number((panel.studioState.karaokeTiming || {}).timed || 0)>0 ? Theme.green : Theme.amber
                                    font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.25
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 1
                                    Repeater { model: ["改词","字体","模板","时码"]; delegate: StudioButton { required property string modelData; required property int index; Layout.fillWidth: true; implicitWidth: 44; implicitHeight: 28; padding: 5; text: modelData; selected: panel.inspectorPage===index; onClicked: panel.inspectorPage=index } }
                                }
                                StackLayout {
                                    Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: panel.inspectorPage
                                    ColumnLayout {
                                        spacing: 7
                                        RowLayout { Layout.fillWidth: true; Text { text: "当前句"; color: Theme.secondary; font.pixelSize: 10 } Item { Layout.fillWidth: true } Text { text: panel.timeLabel(panel.selectedLine.startMs).slice(0,8)+" → "+panel.timeLabel(panel.selectedLine.endMs).slice(0,8); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 9 } }
                                        TextArea {
                                            id: lyricsEdit
                                            objectName: "studioLyricEditor"
                                            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 58
                                            color: Theme.text; placeholderText: "选择歌词后，修改这一句"; placeholderTextColor: Theme.muted
                                            enabled: panel.editable && panel.segments.length>0
                                            wrapMode: TextEdit.Wrap; selectByMouse: true
                                            font.family: "PingFang SC"; font.pixelSize: 14
                                            leftPadding: 11; rightPadding: 11; topPadding: 10; bottomPadding: 10
                                            Accessible.name: "当前句歌词"
                                            Keys.onPressed: function(event) {
                                                if ((event.modifiers & (Qt.ControlModifier | Qt.MetaModifier)) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && panel.editable && panel.segments.length>0 && text !== (panel.selectedLine.text || "")) {
                                                    panel.studioModel.updateSegment(panel.selectedSegment, text);
                                                    event.accepted = true;
                                                }
                                            }
                                            background: Rectangle { radius: 10; color: Theme.canvas; border.color: lyricsEdit.activeFocus ? Theme.focus : "#353e4a"; border.width: lyricsEdit.activeFocus ? 2 : 1 }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text { Layout.fillWidth: true; text: lyricsEdit.text !== (panel.selectedLine.text || "") ? "修改后点击应用" : panel.selectedLine.timingQuality === "character" ? "逐字时码" : panel.selectedLine.timingQuality === "word" ? "词级时码" : panel.segments.length ? "句级时码" : "等待字幕"; color: lyricsEdit.text !== (panel.selectedLine.text || "") ? Theme.amber : Theme.muted; font.pixelSize: 10 }
                                            StudioButton { text: "应用修改"; tone: "primary"; implicitHeight: 29; enabled: panel.editable && panel.segments.length>0 && lyricsEdit.text !== (panel.selectedLine.text || ""); onClicked: panel.studioModel.updateSegment(panel.selectedSegment, lyricsEdit.text) }
                                        }
                                        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
                                        StudioButton { Layout.fillWidth: true; text: "参考纠错"; iconName: "correct"; implicitHeight: 29; hint: "导入参考文本，校正现有歌词"; enabled: panel.editable && panel.segments.length>0; onClicked: panel.studioModel.chooseReference() }
                                        StudioButton { objectName:"studioAICorrection"; Layout.fillWidth:true; text:"国内模型文字纠错"; iconName:"correct"; implicitHeight:30; enabled:panel.editable && panel.segments.length>0; onClicked:panel.apiRequested("text") }
                                    }

                                    StudioStyleInspector { studioModel: panel.studioModel; stateData: panel.studioState; page: "font" }
                                    StudioStyleInspector { id:templateInspector; studioModel: panel.studioModel; stateData: panel.studioState; page: "template" }
                                    StudioLyricTimingInspector { studioModel: panel.studioModel; stateData: panel.studioState; selectedSegment: panel.selectedSegment }
                                }
                            }
                            ScrollView {
                                id: audioInspector; contentWidth: availableWidth; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                ColumnLayout {
                                    width: audioInspector.availableWidth; spacing: 12
                                    Text { Layout.fillWidth: true; text: panel.studioState.audioTitle || "尚未导入主歌曲"; color: Theme.text; font.pixelSize: 13; wrapMode: Text.Wrap }
                                    Text { text: "时长 "+panel.timeLabel(panel.studioState.durationMs); color: Theme.secondary; font.family: Theme.numberFont; font.pixelSize: 10 }
                                    StudioButton { Layout.fillWidth: true; text: panel.studioState.audioPath ? "制作下一首" : "导入歌曲"; iconName: "audio"; enabled: panel.editable; onClicked: if(panel.commitPendingLyrics()) panel.studioModel.chooseAudio() }
                                    StudioButton { objectName: "studioAnalyzeFromInspector"; Layout.fillWidth: true; text: "分析波形与节拍"; enabled: panel.editable && !!panel.studioState.audioPath; onClicked: panel.studioModel.analyzeAudio() }
                                    Text { Layout.fillWidth: true; text: panel.studioState.analysisStatus || "分析后，波形与节拍会显示在时间轴。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
                                    StudioButton { Layout.fillWidth: true; text: "云端识别歌词"; enabled: panel.editable && !!panel.studioState.audioPath; onClicked: panel.apiRequested("speech") }
                                    Text { Layout.fillWidth: true; text: "主歌曲保持原始时钟，附加音轨在各自轨道设置增益。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
                                }
                            }
                            ColumnLayout {
                                spacing: 12
                                Text { Layout.fillWidth: true; text: panel.studioState.backgroundTitle || "纯色背景"; color: Theme.text; font.pixelSize: 14; wrapMode: Text.Wrap }
                                StudioButton { Layout.fillWidth: true; text: "更换默认背景"; iconName: "background"; enabled: panel.editable && !!panel.studioState.audioPath; onClicked: panel.studioModel.chooseBackground() }
                                Text { Layout.fillWidth: true; text: "更多分镜素材从左侧素材库加入。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
                                Item { Layout.fillHeight: true }
                            }
                            StudioCompositionInspector { studioModel: panel.studioModel; stateData: panel.studioState; kind: panel.selectedObject; itemId: panel.selectedItemId }
                            ScrollView {
                                id: sourceInspector; contentWidth: availableWidth; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                ColumnLayout {
                                    width: sourceInspector.availableWidth; spacing: 12
                                    Text { Layout.fillWidth: true; text: panel.selectedSource.title || panel.selectedSource.label || panel.fileName((panel.selectedSource.identity || {}).path || panel.selectedSource.path || "选择一个素材"); color: Theme.text; font.pixelSize: 13; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideMiddle }
                                    Text { Layout.fillWidth: true; text: panel.selectedSource.kind==="image" ? "图片素材" : panel.timeLabel(panel.selectedSource.durationMs)+" · "+(panel.selectedSource.kind==="audio" ? "音频素材" : "视频素材"); color: Theme.secondary; font.pixelSize: 10 }
                                    StudioButton { objectName: "studioAddSelectedClip"; Layout.fillWidth: true; text: "加入画面轨道"; enabled: panel.editable && !!panel.selectedSourceId && panel.selectedSource.kind!=="audio"; onClicked: { panel.studioModel.addClip(panel.selectedSourceId); panel.selectNewest("clip"); } }
                                    StudioButton { objectName: "studioAddSelectedAudio"; Layout.fillWidth: true; text: "加入附加音轨"; enabled: panel.editable && !!panel.selectedSourceId && (panel.selectedSource.kind==="audio" || !!panel.selectedSource.hasAudio); onClicked: { panel.studioModel.addAudioClip(panel.selectedSourceId); panel.selectNewest("audioClip"); } }
                                    Text { Layout.fillWidth: true; text: "加入后，在时间轴选择片段可调整入点、时长、速度与锁定状态。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
                                }
                            }
                            StudioConcertInspector { studioModel: panel.studioModel; stateData: panel.studioState; selectedRow: panel.selectedConcertRow }
                            StudioExportInspector { studioModel: panel.studioModel; stateData: panel.studioState; commitEdits: panel.commitPendingLyrics }
                        }
                    }
                }
            }
        }
        StudioTimeline {
            visible: panel.hasAudio
            Layout.topMargin: 8
            Layout.fillWidth: true; Layout.preferredHeight: !panel.studioState.audioPath ? 85 : panel.height < 680 ? 180 : 220
            stateData: panel.showRendered && panel.renderedPlayer ? Object.assign({}, panel.studioState, {positionMs: panel.renderedPosition}) : panel.studioState
            selectedSegment: panel.selectedSegment
            selectedObject: panel.selectedObject
            selectedItemId: panel.selectedItemId
            onItemSelected: function(kind,id) { panel.selectedItemId=id; panel.selectObject(kind); }
            onClipMoveRequested: function(id,startMs) { if(panel.editable) panel.studioModel.editClip(id,"startMs",startMs); }
            onClipRangeRequested: function(id,startMs,durationMs,sourceInMs) { if(panel.editable) panel.studioModel.editClipRange(id,startMs,durationMs,sourceInMs); }
            onAnalyzeRequested: if(panel.editable) panel.studioModel.analyzeAudio()
            onMarkerRequested: function(position) { if(panel.editable) panel.studioModel.addMarker(position,"标记 "+(((panel.composition.markers || []).length)+1)); }
            onSeekRequested: function(position) {
                if (panel.editable) {
                    if (panel.showRendered && panel.renderedPlayer) panel.renderedPlayer.position = position;
                    panel.studioModel.seek(position);
                }
            }
            onSegmentSelected: function(index) { panel.selectLine(index, false); }
            onObjectSelected: function(kind) { panel.selectObject(kind); }
        }
    }
}
