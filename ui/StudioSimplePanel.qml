import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtMultimedia

Item {
    id: simple
    objectName: "studioSimpleWorkspace"
    property var studioModel: null
    readonly property var stateData: studioModel ? studioModel.state : ({})
    readonly property var settings: stateData.settings || ({})
    readonly property var rows: stateData.segments || []
    readonly property bool hasAudio: !!stateData.audioPath
    readonly property bool editable: !!studioModel && !stateData.busy
    readonly property bool canExport: editable && hasAudio && !!stateData.engineReady && (rows.length>0 || ((stateData.composition || {}).clips || []).length>0 || ((stateData.composition || {}).texts || []).length>0)
    readonly property bool compact: width < 1150
    readonly property bool railSources: width < 1360
    readonly property bool hasLyricDraft: editingLine>=0 && lyricEdit.text!==originalText
    readonly property string resultSource: String(stateData.resultUrl || stateData.savedVideoUrl || "")
    readonly property bool showingPrevious: showResult && !stateData.resultUrl && !stateData.savedVideoCurrent
    readonly property var movie: resultLoader.item ? resultLoader.item.player : null
    readonly property bool playing: showResult ? !!movie && !movie.priming && movie.playbackState === MediaPlayer.PlayingState : !!stateData.playbackPlaying
    readonly property real position: showResult && movie ? movie.position : Number(stateData.positionMs || 0)
    readonly property real duration: Math.max(1,Number(stateData.durationMs || 0))
    property bool showResult: false
    property string observedResult: ""
    property string observedAudio: ""
    property int selectedLine: 0
    property int editingLine: -1
    property string originalText: ""
    property real originalStart: 0
    property real originalEnd: 0
    property string playbackError: ""
    property bool browsingStyles: false
    property int effectsPage: 0
    component PanelTab: Button {
        id: tab
        property bool selected: false
        implicitHeight: 40
        hoverEnabled: true; activeFocusOnTab: true
        Accessible.name: text
        contentItem: Text { text: tab.text; color: tab.selected ? Theme.text : tab.hovered ? Theme.text : Theme.secondary; font.pixelSize: 13; font.weight: tab.selected ? Font.Medium : Font.Normal; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle {
            color: tab.hovered && !tab.selected ? Theme.hoverSurface : "transparent"
            border.color: tab.activeFocus ? Theme.focus : "transparent"; border.width: 2
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
            AccentBar { visible: tab.selected; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: Math.min(48, parent.width - 16); height: 2 }
        }
    }
    signal advancedRequested()
    signal liveRequested()
    signal apiRequested(string kind)

    function openEffects() {
        effectsPage=0;
        templateGallery.category="全部";
        browsingStyles=true;
        showResult=false;
        if(simpleStyleScroll.contentItem) simpleStyleScroll.contentItem.contentY=0;
    }

    function commitEditing() {
        if(editingLine<0) return true;
        var index=editingLine, text=lyricEdit.text;
        if(!studioModel.updateSegment(index,text)) return false;
        editingLine=-1;
        return true;
    }
    function saveProgress(asCopy) {
        if(commitEditing()) asCopy ? studioModel.saveProjectAs() : studioModel.saveProject();
    }
    function exportVideo() { if(commitEditing()) studioModel.exportVideoTo(); }
    function fileName(path) { return String(path || "").split(/[\\/]/).pop(); }
    function timeLabel(ms) {
        var seconds=Math.floor(Math.max(0,Number(ms || 0))/1000);
        return Math.floor(seconds/60).toString().padStart(2,"0")+":"+(seconds%60).toString().padStart(2,"0");
    }
    function togglePlayback() {
        if(showResult && movie) {
            var wasPlaying=playing;
            movie.priming=false;
            wasPlaying ? movie.pause() : movie.play();
        }
        else if(studioModel) studioModel.togglePreview();
    }
    function jumpToLine(index) {
        if(!commitEditing()) return;
        selectedLine=index; showResult=false;
        var row=rows[index];
        if(row && editable) studioModel.seek(Number(row.startMs)+Math.min(200,(Number(row.endMs)-Number(row.startMs))/2));
    }
    function startEditing() {
        if(!commitEditing()) return;
        var row=rows[selectedLine];
        if(!row) return;
        originalText=String(row.text); originalStart=Number(row.startMs); originalEnd=Number(row.endMs);
        editingLine=selectedLine; lyricEdit.text=originalText; lyricEdit.forceActiveFocus();
    }
    onStateDataChanged: {
        var audio=String(stateData.audioPath || "");
        if(audio!==observedAudio) { observedAudio=audio; editingLine=-1; selectedLine=0; playbackError=""; }
        selectedLine=Math.max(0,Math.min(selectedLine,Math.max(0,rows.length-1)));
        if(editingLine>=0) {
            var row=rows[editingLine];
            if(!row || row.text!==originalText || Number(row.startMs)!==originalStart || Number(row.endMs)!==originalEnd) editingLine=-1;
        }
        if(editingLine<0 && Number(stateData.currentSegment)>=0) selectedLine=Number(stateData.currentSegment);
        var result=String(stateData.resultUrl || "");
        if(result && result!==observedResult) { playbackError=""; showResult=true; }
        if(!result) showResult=false;
        observedResult=result;
    }
    onShowResultChanged: {
        if(studioModel) studioModel.pausePreview();
        if(!showResult && movie) movie.pause();
    }
    onVisibleChanged: {
        if(!visible) {
            if(studioModel) studioModel.pausePreview();
            if(movie) movie.pause();
        }
    }
    Shortcut {
        sequence: "Space"
        enabled: simple.visible && simple.editable && simple.hasAudio && (!simple.Window.window || !simple.Window.window.activeFocusItem || typeof simple.Window.window.activeFocusItem.cursorPosition === "undefined")
        onActivated: simple.togglePlayback()
    }
    Menu {
        id: projectMenu
        width: 264
        padding: 6
        background: Rectangle { color: Theme.elevated; border.color: Theme.stroke; radius: 10 }
        MenuItem { text: "打开已有工程…"; enabled: simple.editable; onTriggered: if(simple.commitEditing()) simple.studioModel.openProject() }
        MenuItem { text: "另存一份工程…"; enabled: simple.editable && simple.hasAudio; onTriggered: simple.saveProgress(true) }
        MenuSeparator { visible: (simple.stateData.recentProjects || []).length>0 }
        Repeater {
            model: simple.stateData.recentProjects || []
            delegate: MenuItem {
                required property var modelData
                text: modelData.title+(modelData.available ? "" : "（文件不可用）")
                enabled: simple.editable && modelData.available
                onTriggered: if(simple.commitEditing()) simple.studioModel.openProject(modelData.path)
            }
        }
    }

    property int sourceTab: 0
    component ToolLink: StudioButton { implicitHeight: 30; font.pixelSize: 12; padding: 9 }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 10; anchors.topMargin: 6; spacing: 8
        // 歌暮式工具栏：工程动作在左（小图标 + 文字），主操作「导出视频」在最右
        RowLayout {
            objectName: "studioToolbar"
            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 2
            ToolLink { id: projectButton; objectName: "simpleProjectMenu"; text: "工程"; iconName: "project"; enabled: simple.editable; onClicked: projectMenu.popup(projectButton,0,height+5) }
            ToolLink { objectName: "simpleSaveProject"; visible: simple.hasAudio; text: simple.stateData.dirty || simple.hasLyricDraft ? "保存进度" : simple.stateData.projectPath ? "已保存" : "保存进度"; iconName: "save"; hint: simple.stateData.projectPath ? "当前工程："+simple.fileName(simple.stateData.projectPath) : "新工程，保存时选择新位置"; enabled: simple.editable; onClicked: simple.saveProgress(false) }
            Rectangle { visible: simple.hasAudio; Layout.preferredWidth: 1; Layout.preferredHeight: 16; color: Theme.hairline; Layout.leftMargin: 6; Layout.rightMargin: 6 }
            ToolLink { visible: simple.hasAudio; iconName: "audio"; text: "换歌"; enabled: simple.editable; onClicked: if(simple.commitEditing()) simple.studioModel.chooseAudio() }
            ToolLink { visible: simple.hasAudio; iconName: "lyrics"; text: "导入歌词"; enabled: simple.editable; onClicked: if(simple.commitEditing()) simple.studioModel.chooseSubtitles() }
            ToolLink { visible: simple.hasAudio; iconName: "background"; text: "换背景"; enabled: simple.editable; onClicked: simple.studioModel.chooseBackground() }
            ToolLink { objectName: "simpleMoreEditing"; iconName: "tools"; text: "更多编辑"; enabled: simple.editable; hint: "时间轴、完整模板和详细设置"; onClicked: if(simple.commitEditing()) simple.advancedRequested() }
            Item { Layout.fillWidth: true }
            Text { visible: !simple.compact; Layout.maximumWidth: 260; text: !simple.hasAudio ? "选歌、加歌词，试听后直接导出。" : !simple.rows.length ? "歌曲已选好，接下来添加歌词。" : "歌词已就绪，播放检查后即可导出。"; color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.rightMargin: 8 }
            ActionButton { objectName: "simpleExportVideo"; visible: simple.hasAudio; text: "导出视频"; tone: "primary"; implicitHeight: 30; font.pixelSize: 12; padding: 14; enabled: simple.canExport; onClicked: simple.exportVideo() }
        }

        RowLayout {
            visible: !!simple.stateData.busy || !!simple.stateData.error || !!simple.stateData.savedVideoPath || !!simple.stateData.recoveryPath
            Layout.fillWidth: true; spacing: 10
            Text {
                Layout.fillWidth: true; font.pixelSize: 12; elide: Text.ElideMiddle
                text: simple.stateData.error || (simple.stateData.busy ? /CPU|渲染|生成完整影片/.test(String(simple.stateData.status)) ? "正在生成视频，完成后会保存到选定位置。" : simple.stateData.status : simple.stateData.savedVideoPath ? simple.stateData.savedVideoCurrent && !simple.hasLyricDraft ? "视频已保存："+simple.fileName(simple.stateData.savedVideoPath) : "有新修改，需重新导出。可查看上次视频。" : "上一工程的未保存修改已保留。")
                color: simple.stateData.error ? Theme.red : simple.stateData.savedVideoPath ? simple.stateData.savedVideoCurrent && !simple.hasLyricDraft ? Theme.green : Theme.amber : Theme.secondary
            }
            ProgressBar { visible: !!simple.stateData.busy; Layout.preferredWidth: 120; from: 0; to: 1; value: Number(simple.stateData.progress || 0); indeterminate: value<=0 }
            StudioButton { visible: !!simple.stateData.busy; text: "取消"; onClicked: simple.studioModel.cancelJob() }
            StudioButton { objectName: "simpleViewSavedVideo"; visible: !simple.stateData.busy && !!simple.stateData.savedVideoPath; text: simple.stateData.savedVideoCurrent && !simple.hasLyricDraft ? "查看视频" : "查看上次视频"; onClicked: if(simple.commitEditing()) { simple.studioModel.pausePreview(); simple.showResult=!!simple.resultSource; } }
            StudioButton { visible: !simple.stateData.busy && !!simple.stateData.recoveryPath; text: "恢复修改"; onClicked: if(simple.commitEditing()) simple.studioModel.openProject(simple.stateData.recoveryPath) }
        }

        Item {
            visible: !simple.hasAudio
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.centerIn: parent; width: Math.min(parent.width-40,520); spacing: 14
                // 歌暮式：一张大导入卡就是起点，不放大标题
                ImportCard {
                    objectName: "simpleStartAudio"
                    Layout.fillWidth: true; Layout.preferredHeight: 210
                    text: "点击选择歌曲"; iconName: "audio"
                    hint: "传统 KTV 字幕已经配好，选好歌就能加歌词。"
                    enabled: simple.editable
                    onClicked: simple.studioModel.chooseAudio()
                }
                RowLayout {
                    objectName: "simpleFlowSteps"
                    Layout.alignment: Qt.AlignHCenter; spacing: 6
                    Accessible.role: Accessible.StaticText
                    Accessible.name: "四步：选歌、加歌词、试听、导出"
                    Repeater {
                        model: ["选歌", "加歌词", "试听", "导出"]
                        delegate: RowLayout {
                            required property string modelData
                            required property int index
                            spacing: 6
                            Text { visible: index > 0; text: "·"; color: Theme.muted; font.pixelSize: 12 }
                            Text { text: String(index + 1) + " " + modelData; color: index === 0 ? Theme.accent : Theme.muted; font.pixelSize: 12; font.weight: index === 0 ? Font.Medium : Font.Normal }
                        }
                    }
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 4; spacing: 8
                    ActionButton { objectName: "simpleWelcomeOpen"; text: "打开已有工程"; enabled: simple.editable; onClicked: simple.studioModel.openProject() }
                    ActionButton { objectName: "simpleOpenExample"; text: "先试用示例"; enabled: simple.editable; onClicked: simple.studioModel.openExampleProject() }
                    StudioButton { objectName: "simpleWelcomeLive"; text: "去现场跟随"; implicitHeight: 36; onClicked: simple.liveRequested() }
                }
                ColumnLayout {
                    visible: (simple.stateData.recentProjects || []).length>0
                    Layout.fillWidth: true; Layout.topMargin: 16; spacing: 5
                    Text { text: "继续上次制作"; color: Theme.muted; font.pixelSize: 12 }
                    Repeater {
                        model: (simple.stateData.recentProjects || []).slice(0,3)
                        delegate: StudioButton {
                            required property var modelData
                            required property int index
                            objectName: "simpleRecent-"+index
                            Layout.fillWidth: true; implicitHeight: 38; textAlignment: Text.AlignLeft
                            text: modelData.title+(modelData.available ? "" : "（文件不可用）")
                            hint: modelData.path; enabled: simple.editable && modelData.available
                            onClicked: simple.studioModel.openProject(modelData.path)
                        }
                    }
                }
            }
        }

        // 歌暮式三栏：左素材、中播放器、右歌词与字幕。小窗口时左栏收成图标窄栏，给预览让位。
        RowLayout {
            objectName: "simpleThreeColumns"
            visible: simple.hasAudio
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
            Rectangle {
                objectName: "simpleSourceColumn"
                Layout.fillHeight: true
                Layout.preferredWidth: simple.railSources ? 76 : 236
                color: Theme.surface; radius: Theme.radiusCard; border.color: Theme.hairline; clip: true
                // 歌暮左栏：顶部图标标签「素材 / 识别」，窄窗只留素材图标
                RowLayout {
                    id: sourceTabs
                    visible: !simple.railSources
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 48; spacing: 0
                    Repeater {
                        model: [{label:"素材", icon:"library"}, {label:"识别纠错", icon:"prepare"}]
                        delegate: Button {
                            id: srcTab
                            required property var modelData
                            required property int index
                            objectName: "simpleSourceTab-" + index
                            Layout.fillWidth: true; Layout.fillHeight: true
                            readonly property bool selected: simple.sourceTab === index
                            hoverEnabled: true; activeFocusOnTab: true; Accessible.name: modelData.label
                            onClicked: simple.sourceTab = index
                            contentItem: Column {
                                spacing: 3
                                AppIcon { anchors.horizontalCenter: parent.horizontalCenter; width: 18; height: 18; name: srcTab.modelData.icon; ink: srcTab.selected ? Theme.text : Theme.muted }
                                Text { anchors.horizontalCenter: parent.horizontalCenter; text: srcTab.modelData.label; color: srcTab.selected ? Theme.text : Theme.secondary; font.pixelSize: 11; font.weight: srcTab.selected ? Font.Medium : Font.Normal }
                            }
                            background: Rectangle {
                                color: srcTab.selected || srcTab.hovered ? Theme.wash : "transparent"
                                border.color: srcTab.activeFocus ? Theme.focus : "transparent"; border.width: 2
                                AccentBar { visible: srcTab.selected; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: 24; height: 2 }
                            }
                        }
                    }
                }
                Rectangle { visible: sourceTabs.visible; anchors.left: parent.left; anchors.right: parent.right; y: 48; height: 1; color: Theme.hairline }
                ColumnLayout {
                    visible: sourceTabs.visible && simple.sourceTab === 1
                    anchors.fill: parent; anchors.margins: 12; anchors.topMargin: 60; spacing: 10
                    Text { Layout.fillWidth: true; text: "没有歌词文件时，用百炼识别歌曲；已有歌词可用模型检查错字。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.3 }
                    ActionButton { objectName: "simpleRecognizeSpeech"; Layout.fillWidth: true; text: "百炼识别歌词"; tone: simple.rows.length ? "neutral" : "primary"; implicitHeight: 34; font.pixelSize: 12; enabled: simple.editable; onClicked: simple.apiRequested("speech") }
                    ActionButton { objectName: "simpleRecognizeText"; Layout.fillWidth: true; text: "文字纠错"; implicitHeight: 34; font.pixelSize: 12; enabled: simple.editable && simple.rows.length>0; onClicked: simple.apiRequested("text") }
                    Item { Layout.fillHeight: true }
                }
                ColumnLayout {
                    visible: !sourceTabs.visible || simple.sourceTab === 0
                    anchors.fill: parent; anchors.margins: simple.railSources ? 8 : 12; anchors.topMargin: sourceTabs.visible ? 60 : 8; spacing: simple.railSources ? 6 : 8
                    SourceTile {
                        objectName: "simpleChangeAudio"
                        Layout.fillWidth: true; rail: simple.railSources
                        iconName: "audio"; title: "歌曲"; done: true
                        detail: simple.fileName(simple.stateData.audioTitle || simple.stateData.audioPath)
                        text: "制作下一首"
                        enabled: simple.editable
                        onClicked: if(simple.commitEditing()) simple.studioModel.chooseAudio()
                    }
                    SourceTile {
                        objectName: "simpleAddLyrics"
                        Layout.fillWidth: true; rail: simple.railSources
                        iconName: "lyrics"; title: "歌词"; done: simple.rows.length>0
                        tone: simple.rows.length ? "neutral" : "primary"
                        detail: simple.rows.length ? simple.rows.length+" 句" : "导入 LRC / SRT 歌词文件"
                        text: simple.rows.length ? "更换" : "添加歌词"
                        enabled: simple.editable
                        onClicked: if(simple.commitEditing()) simple.studioModel.chooseSubtitles()
                    }
                    SourceTile {
                        objectName: "simpleAutoRecognize"
                        visible: !simple.rows.length
                        Layout.fillWidth: true; rail: simple.railSources
                        iconName: "correct"; title: "识别"
                        detail: "没有歌词文件时，用百炼识别"
                        text: "云端识别"
                        enabled: simple.editable
                        onClicked: simple.apiRequested("speech")
                    }
                    SourceTile {
                        objectName: "simpleBackground"
                        Layout.fillWidth: true; rail: simple.railSources
                        iconName: "background"; title: "背景"; done: !!simple.stateData.backgroundPath
                        detail: simple.stateData.backgroundTitle || (simple.stateData.backgroundPath ? simple.fileName(simple.stateData.backgroundPath) : "纯色背景")
                        text: "换背景"
                        enabled: simple.editable
                        onClicked: simple.studioModel.chooseBackground()
                    }
                    Item { Layout.fillHeight: true }
                    Text { visible: !simple.railSources; Layout.fillWidth: true; text: "选歌 → 加歌词 → 试听 → 导出"; color: Theme.muted; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
                }
            }
            Rectangle {
                objectName: "simplePlayerColumn"
                Layout.fillWidth: true; Layout.fillHeight: true
                color: Theme.surface; radius: Theme.radiusCard; border.color: Theme.hairline; clip: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 8
                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 30
                    Text { text: simple.showResult ? "导出结果" : "播放器"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; Layout.leftMargin: 4 }
                    Text { Layout.fillWidth: true; text: simple.hasAudio ? simple.fileName(simple.stateData.audioTitle).replace(/\.[^.]+$/, "") : ""; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideMiddle }
                    StudioButton { objectName: "simplePreviewToggle"; visible: simple.showResult || !!simple.resultSource; text: simple.showResult ? "返回编辑预览" : simple.stateData.resultUrl || simple.stateData.savedVideoCurrent ? "看导出结果" : "查看上次视频"; implicitHeight: 30; onClicked: if(simple.commitEditing()) { simple.studioModel.pausePreview(); simple.showResult=!simple.showResult; } }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    color: Theme.sunken; border.color: "transparent"; radius: 6; clip: true
                    Image {
                        objectName: "simplePreviewImage"
                        anchors.fill: parent; anchors.margins: 1
                        visible: simple.visible && !simple.showResult
                        cache: false; asynchronous: true; retainWhileLoading: true
                        sourceSize: Qt.size(Math.min(960,Number(simple.settings.width || 854)),Math.min(960,Number(simple.settings.width || 854))*Number(simple.settings.height || 480)/Number(simple.settings.width || 854))
                        source: visible && simple.hasAudio ? "image://studioPreview/frame?revision="+String(simple.stateData.previewRevision)+"&position="+Number(simple.stateData.positionMs || 0) : ""
                        fillMode: Image.PreserveAspectFit
                    }
                    Text { visible: !simple.showResult && !simple.rows.length; anchors.centerIn: parent; width: parent.width-40; text: "添加歌词后，就能在这里跟唱预览。"; color: Theme.secondary; font.pixelSize: 15; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter }
                    Loader {
                        id: resultLoader
                        anchors.fill: parent
                        active: simple.visible && simple.showResult && !!simple.resultSource
                        sourceComponent: Item {
                            property alias player: resultPlayer
                            VideoOutput { id: output; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectFit }
                            MediaPlayer {
                                id: resultPlayer
                                objectName: "simpleResultPlayer"
                                property bool priming: false
                                source: simple.resultSource
                                videoOutput: output
                                audioOutput: AudioOutput { volume: .7; muted: resultPlayer.priming }
                                onMediaStatusChanged: {
                                    if(mediaStatus===MediaPlayer.LoadedMedia) {
                                        priming=true; position=Math.min(duration,Number(simple.stateData.positionMs || 0)); play();
                                    }
                                }
                                onErrorOccurred: function(error,message) { simple.playbackError=message; }
                            }
                            Connections {
                                target: output.videoSink
                                function onVideoFrameChanged() {
                                    if(resultPlayer.priming) { resultPlayer.pause(); resultPlayer.priming=false; }
                                }
                            }
                        }
                    }
                }
                Slider { objectName: "simplePosition"; Layout.fillWidth: true; Layout.preferredHeight: 20; from: 0; to: simple.duration; value: simple.position; enabled: simple.editable; Accessible.name: "播放进度"; onMoved: { if(simple.showResult && simple.movie) simple.movie.position=value; else simple.studioModel.seek(value); } }
                // 歌暮播放条：时间在左，播放居中，静音在右
                Item {
                    Layout.fillWidth: true; Layout.preferredHeight: 40
                    Row {
                        anchors.left: parent.left; anchors.leftMargin: 4; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        Text { text: simple.timeLabel(simple.position); color: Theme.accent; font.pixelSize: 12; font.family: Theme.numberFont }
                        Text { text: "/"; color: Theme.muted; font.pixelSize: 12 }
                        Text { text: simple.timeLabel(simple.duration); color: Theme.muted; font.pixelSize: 12; font.family: Theme.numberFont }
                    }
                    StudioButton { objectName: "simplePlay"; anchors.centerIn: parent; text: simple.playing ? "暂停" : "播放"; iconName: simple.playing ? "pause" : "play"; tone: "primary"; implicitHeight: 36; implicitWidth: 104; enabled: simple.editable; onClicked: simple.togglePlayback() }
                    StudioButton { objectName: "simpleMute"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: simple.stateData.playbackMuted ? "恢复声音" : "静音"; visible: !simple.showResult; implicitHeight: 30; font.pixelSize: 12; enabled: simple.editable; onClicked: simple.studioModel.togglePreviewMute() }
                }
                Text {
                    Layout.fillWidth: true; font.pixelSize: 12; elide: Text.ElideRight
                    color: simple.stateData.playbackError || simple.playbackError ? Theme.red : Theme.muted
                    text: simple.stateData.playbackError || simple.playbackError || (simple.showResult ? simple.showingPrevious ? "上次导出的视频，尚未包含当前修改。" : "已生成的视频，可直接播放检查。" : ((simple.stateData.composition || {}).audioClips || []).length ? "此处试听主歌曲，完整混音可在导出结果中检查。" : "空格播放或暂停，点击歌词就能跳到那一句。")
                }
            }
            }
            Rectangle {
                objectName: "simpleLyricColumn"
                Layout.fillHeight: true
                Layout.preferredWidth: simple.compact ? 272 : 318; Layout.minimumWidth: 262; Layout.maximumWidth: simple.compact ? 272 : 318
                color: Theme.surface; radius: Theme.radiusCard; border.color: Theme.hairline; clip: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; anchors.topMargin: 0; spacing: 10
                // 歌暮右栏标签：紫字 + 底部紫线
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: -12; Layout.rightMargin: -12; spacing: 0
                    PanelTab { objectName: "simpleReturnLyrics"; Layout.fillWidth: true; text: simple.rows.length ? "歌词 · "+simple.rows.length : "歌词"; selected: !simple.browsingStyles; onClicked: simple.browsingStyles=false }
                    PanelTab { objectName: "simpleEffectsButton"; Layout.fillWidth: true; text: "字幕特效"; selected: simple.browsingStyles; onClicked: simple.openEffects() }
                }
                RowLayout {
                    visible: !simple.browsingStyles
                    Layout.fillWidth: true; spacing: 6
                Text {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 11; lineHeight: 1.3
                    text: !simple.rows.length ? "有歌词文件可以直接导入，也可以用云端识别。" : !simple.settings.lyricSweep ? "歌词走带已关闭，按演唱时间自动换句。" : Number((simple.stateData.karaokeTiming || {}).timed || 0)>=simple.rows.length ? "跟唱字幕已就绪" : Number((simple.stateData.karaokeTiming || {}).timed || 0)>0 ? "跟唱已就绪，部分句子按句估算扫色。" : "按句估算扫色；补充词时码后可更贴合人声。"
                    color: simple.rows.length ? Theme.secondary : Theme.muted
                }
                    StudioButton { objectName: "simpleEditLyrics"; visible: simple.rows.length>0; Layout.alignment: Qt.AlignTop; text: "改词"; tone: "primary"; implicitHeight: 28; font.pixelSize: 12; enabled: simple.editable; onClicked: simple.startEditing() }
                }
                RowLayout {
                    visible: simple.browsingStyles; Layout.fillWidth: true; spacing: 6
                    StudioButton { objectName:"simpleEffectsStyles"; Layout.fillWidth:true; text:"字幕样式"; selected:simple.effectsPage===0; onClicked:simple.effectsPage=0 }
                    StudioButton { objectName:"simpleEffectsAnimations"; Layout.fillWidth:true; text:"文字动画"; selected:simple.effectsPage===1; onClicked:simple.effectsPage=1 }
                }
                ScrollView {
                    id: simpleStyleScroll
                    objectName: "simpleStyleBrowser"
                    visible: simple.browsingStyles
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 80
                    contentWidth: availableWidth; clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: simpleStyleScroll.availableWidth
                        StudioTemplateGallery { id:templateGallery; visible:simple.effectsPage===0; controlPrefix: "simple-"; largePreviews: true; studioModel: simple.studioModel; stateData: simple.stateData }
                        StudioEffectChoices { visible:simple.effectsPage===1; studioModel:simple.studioModel; stateData:simple.stateData }
                    }
                }
                ListView {
                    id: lyricList
                    visible: !simple.browsingStyles
                    objectName: "simpleLyricList"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 80
                    clip: true; spacing: 4; model: simple.rows
                    currentIndex: simple.stateData.playbackPlaying && Number(simple.stateData.currentSegment)>=0 ? Number(simple.stateData.currentSegment) : simple.selectedLine
                    onCurrentIndexChanged: if(currentIndex>=0 && simple.editingLine<0) positionViewAtIndex(currentIndex,ListView.Contain)
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: ItemDelegate {
                        id: lyricRow
                        required property var modelData
                        required property int index
                        objectName: "simpleLyric-"+index
                        width: ListView.view.width; height: Math.max(60,lyricText.implicitHeight+28)
                        enabled: simple.editable
                        Accessible.role: Accessible.Button
                        Accessible.name: "第"+(index+1)+"句："+modelData.text
                        onClicked: simple.jumpToLine(index)
                        contentItem: RowLayout {
                            spacing: 8
                            Text { Layout.alignment: Qt.AlignTop; text: simple.timeLabel(lyricRow.modelData.startMs); color: Theme.muted; font.pixelSize: 10; font.family: Theme.numberFont; Layout.topMargin: 2 }
                            Text { id: lyricText; Layout.fillWidth: true; text: lyricRow.modelData.text; textFormat: Text.PlainText; color: lyricRow.ListView.isCurrentItem ? Theme.text : Theme.secondary; font.pixelSize: 14; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; lineHeight: 1.25 }
                        }
                        background: Rectangle { radius: 7; color: lyricRow.ListView.isCurrentItem ? Theme.accentSoft : lyricRow.hovered ? Theme.hoverSurface : "transparent"; border.width: lyricRow.activeFocus ? 2 : 0; border.color: Theme.accent }
                    }
                }
                ColumnLayout {
                    visible: !simple.browsingStyles && simple.editingLine>=0
                    Layout.fillWidth: true; spacing: 6
                    Text { text: "修改第 "+(simple.editingLine+1)+" 句"; color: Theme.secondary; font.pixelSize: 12 }
                    TextArea { id: lyricEdit; objectName: "simpleLyricEditor"; Layout.fillWidth: true; Layout.preferredHeight: 72; color: Theme.text; font.pixelSize: 14; wrapMode: Text.Wrap; selectByMouse: true; enabled: simple.editable; background: Rectangle { radius: 6; color: Theme.surface; border.color: lyricEdit.activeFocus ? Theme.focus : Theme.stroke } }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        StudioButton { text: "取消"; onClicked: simple.editingLine=-1 }
                        StudioButton { objectName: "simpleApplyLyric"; text: "保存修改"; tone: "primary"; enabled: simple.editable && lyricEdit.text.trim().length>0; onClicked: simple.commitEditing() }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "字幕外观"; color: Theme.secondary; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    StudioButton {
                        objectName:"simpleLyricSweep"; text:"歌词走带"; checkable:true
                        checked:!!simple.settings.lyricSweep; selected:checked; enabled:simple.editable
                        hint:"当前模板开启或关闭跟唱扫色 · Ctrl+Alt+K"
                        onClicked:simple.studioModel.setSetting("lyricSweep",checked)
                        Accessible.onToggleAction:simple.studioModel.setSetting("lyricSweep",!simple.settings.lyricSweep)
                    }
                    StudioButton { objectName: "simpleBrowseStyles"; text: simple.browsingStyles ? "收起特效" : "字幕特效"; selected: simple.browsingStyles; onClicked: simple.browsingStyles ? simple.browsingStyles=false : simple.openEffects() }
                }
                RowLayout {
                    visible: !simple.browsingStyles
                    Layout.fillWidth: true; spacing: 6
                    StudioButton { objectName: "simpleStyleClassic"; Layout.fillWidth: true; implicitWidth: 110; text: "传统 KTV"; selected: simple.settings.templateId==="karaoke"; enabled: simple.editable; onClicked: simple.studioModel.applyKaraokeStyle() }
                    StudioButton { objectName: "simpleStyleCard"; Layout.fillWidth: true; implicitWidth: 110; text: "简洁双行"; selected: simple.settings.templateId==="karaoke-card"; enabled: simple.editable; onClicked: simple.studioModel.applyTemplate("karaoke-card") }
                }
                Text { visible: !simple.browsingStyles && simple.settings.templateId!=="karaoke" && simple.settings.templateId!=="karaoke-card"; Layout.fillWidth: true; text: "当前样式："+((simple.stateData.templates || []).filter(function(row){return row.id===simple.settings.templateId;})[0] || {}).label; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                RowLayout {
                    visible: !simple.browsingStyles
                    Layout.fillWidth: true
                    Text { text: "文字大小"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 68 }
                    DarkComboBox { objectName: "simpleFontSize"; Layout.fillWidth: true; implicitHeight: 32; model: ["标准","大字","特大"]; currentIndex: Number(simple.settings.fontSize)>52 ? 2 : Number(simple.settings.fontSize)>=46 ? 1 : 0; enabled: simple.editable; Accessible.name: "文字大小"; onActivated: function(index) { simple.studioModel.setSetting("fontSize",[40,48,56][index]); } }
                }
                RowLayout {
                    visible: !simple.browsingStyles
                    Layout.fillWidth: true
                    Text { text: "视频画质"; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 68 }
                    DarkComboBox { objectName: "simpleQuality"; Layout.fillWidth: true; implicitHeight: 32; model: ["标准","高清","超清"]; currentIndex: Math.max(0,[854,1280,1920].indexOf(Number(simple.settings.width || 854))); enabled: simple.editable; Accessible.name: "视频画质"; onActivated: function(index) { simple.studioModel.setSetting("width",[854,1280,1920][index]); } }
                }
            }
            }
        }
    }
}
