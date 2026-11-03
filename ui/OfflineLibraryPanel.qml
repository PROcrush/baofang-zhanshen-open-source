import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    property var stateData: ({})
    signal preparationRequested()
    readonly property var libraryData: stateData.offlineLibrary || ({})
    readonly property var batch: libraryData.batch || ({})
    readonly property var tracks: libraryData.tracks || stateData.library || []
    readonly property bool offlineOnly: !!libraryData.offlineOnly
    readonly property bool replay: stateData.inputMode ? stateData.inputMode === "replay" : stateData.mode === "replay"
    readonly property var filteredTracks: filterTracks(tracks, searchField.text, stateFilter)
    // 歌暮式渐进：曲库为空时只给一张导入卡，统计和批量准备等有歌后再出现
    readonly property bool emptyLibrary: tracks.length === 0 && count("total") === 0
    readonly property var setlist: stateData.setlist || ({})
    readonly property var setlistItems: setlist.items || []
    readonly property int setlistWidth: width < 1180 ? 260 : 320
    function inSetlist(version) {
        for (var i = 0; i < setlistItems.length; ++i) if (setlistItems[i].versionId === version) return true;
        return false;
    }
    function setlistTone(state) { return state === "ready" ? "green" : state === "review" ? "amber" : "red"; }
    function filterTracks(rows, query, state) {
        var needle = query.trim().toLowerCase();
        if (state && state !== "all") rows = rows.filter(matchesFilter);
        if (!needle) return rows;
        return rows.filter(function(row) { return ((row.title || "") + " " + (row.path || "") + " " + (row.versionId || "")).toLowerCase().indexOf(needle) >= 0; });
    }
    function count(name) { return Number(libraryData[name] || 0); }
    function batchRow(version) {
        var rows = batch.rows || [];
        for (var i = 0; i < rows.length; ++i) if (rows[i].versionId === version) return rows[i];
        return {};
    }
    function trackTone(track) { return track.offlineReady ? (track.needsReview ? "amber" : "green") : track.offlineState && track.offlineState !== "missing" ? "red" : "amber"; }
    function trackLabel(track) { return track.offlineLabel || (track.isDemo ? "回放演示" : track.offlineReady ? "离线就绪" : "未准备歌词"); }
    function phaseLabel(phase) {
        return ({idle:"待规划", ready:"已缓存", needs_review:"已缓存 · 待复核", planned:"计划就绪", preflight:"检查文件", planning:"检查文件", running:"批次运行中", completed:"批次结束", processing:"识别中", polling:"等待结果", pending:"待准备", waiting:"待准备", reference_ready:"参考已选择", paused:"已暂停", failed:"失败", blocked:"需处理", unknown:"需续查"})[phase] || phase || "待准备";
    }

    readonly property int processingCount: Number(libraryData.indexingCount || 0) + (batch.rows || []).filter(function(row){ return ["preflight","planning","processing","polling","running"].indexOf(row.phase || "") >= 0; }).length
    readonly property int unknownCount: (batch.rows || []).filter(function(row){ return row.phase === "unknown"; }).length

    component StatTile: Rectangle {
        id: tile
        property string label: ""
        property int value: 0
        property string tone: "muted"
        property string caption: ""
        Layout.fillWidth: true
        Layout.preferredHeight: 62
        radius: Theme.radiusCard
        color: Theme.surface
        border.color: value > 0 && tone !== "muted" ? Theme.toneLine(tone) : "transparent"
        Accessible.role: Accessible.StaticText
        Accessible.name: label + " " + value + " 首"
        ColumnLayout {
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 10; anchors.topMargin: 9; anchors.bottomMargin: 9; spacing: 2
            RowLayout {
                spacing: 6
                Rectangle { width: 7; height: 7; radius: 4; color: Theme.toneInk(tile.tone) }
                Text { text: tile.label; color: Theme.secondary; font.pixelSize: 12 }
            }
            RowLayout {
                spacing: 6
                Text { text: String(tile.value); color: tile.value > 0 ? Theme.toneInk(tile.tone === "muted" ? "" : tile.tone) : Theme.muted; font.pixelSize: 20; font.weight: Font.Medium; font.family: Theme.numberFont }
                Text { visible: !!tile.caption; text: tile.caption; color: Theme.muted; font.pixelSize: 11 }
            }
        }
    }

    // 歌暮式摆放：顶部一条工具栏（主操作「导入音频」在最右）；
    // 三栏：左状态筛选，中歌曲列表，右「批量准备 / 今晚歌单」标签。
    property string stateFilter: "all"
    property int rightTab: 0
    function matchesFilter(row) {
        if (stateFilter === "ready") return !!row.offlineReady && !row.needsReview;
        if (stateFilter === "review") return !!row.needsReview;
        if (stateFilter === "missing") return row.offlineState === "missing";
        if (stateFilter === "processing") return ["preflight","planning","processing","polling","running"].indexOf(batchRow(row.versionId).phase || row.preparationPhase || "") >= 0;
        if (stateFilter === "failed") return (!!row.offlineState && ["ready","missing"].indexOf(row.offlineState) < 0) || batchRow(row.versionId).phase === "failed";
        return true;
    }
    component ToolLink: StudioButton { implicitHeight: 30; font.pixelSize: 12; padding: 9 }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10; anchors.topMargin: 6
        spacing: 8
        RowLayout {
            objectName: "libraryToolbar"
            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 2
            ToolLink { objectName: "offlineImportFolder"; iconName: "project"; text: "导入文件夹"; onClicked: controller.importAudioFolder() }
            ToolLink { objectName: "offlineImportTimeline"; iconName: "lyrics"; text: "导入本地字幕"; onClicked: controller.importTimeline() }
            Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 16; color: Theme.hairline; Layout.leftMargin: 6; Layout.rightMargin: 6 }
            ToolLink { objectName: "offlineVerify"; iconName: "check"; text: "核验曲库"; hint: "重新核验离线曲库的实际音频版本"; onClicked: controller.verifyOfflineLibrary() }
            ToolLink { objectName: "offlineExportCache"; iconName: "export"; text: "备份歌词库"; hint: "备份本地时间轴与版本索引，不复制原始音频"; onClicked: controller.exportLyricCache() }
            ToolLink { objectName: "offlineImportCache"; iconName: "import"; text: "导入歌词库"; onClicked: controller.importLyricCache() }
            ToolLink { objectName: "offlineRefresh"; text: "刷新"; onClicked: controller.rescanLibrary() }
            Item { Layout.fillWidth: true }
            DarkCheckBox {
                id: offlineToggle
                objectName: "offlineOnlyToggle"
                text: "离线演出"
                checked: panel.offlineOnly
                ToolTip.visible: hovered; ToolTip.text: "只读取本机缓存，暂停云端新提交和续查"
                onClicked: controller.setOfflineOnly(checked)
            }
            ActionButton { objectName: "offlineImportAudio"; Layout.leftMargin: 8; text: "导入音频（多选）"; tone: "primary"; implicitHeight: 30; font.pixelSize: 12; padding: 12; onClicked: controller.importAudioBatch() }
        }
        RowLayout {
            objectName: "libraryColumns"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
            // 左：状态筛选
            PanelCard {
                objectName: "libraryStatusColumn"
                visible: !panel.emptyLibrary
                Layout.preferredWidth: panel.width < 1180 ? 176 : 212; Layout.fillHeight: true
                ColumnLayout {
                    objectName: "offlineStatusStrip"
                    anchors.fill: parent; anchors.margins: 10; spacing: 2
                    Text { text: "状态"; color: Theme.secondary; font.pixelSize: 12; font.letterSpacing: 1; Layout.leftMargin: 4; Layout.bottomMargin: 6; Layout.topMargin: 4 }
                    FilterRow { objectName: "offlineStat-all"; Layout.fillWidth: true; text: "全部"; tone: "muted"; value: panel.count("total"); selected: panel.stateFilter === "all"; onClicked: panel.stateFilter = "all" }
                    FilterRow { objectName: "offlineStat-ready"; Layout.fillWidth: true; text: "可用"; tone: "green"; value: Math.max(0, panel.count("ready") - panel.count("review")); selected: panel.stateFilter === "ready"; onClicked: panel.stateFilter = "ready" }
                    FilterRow { objectName: "offlineStat-review"; Layout.fillWidth: true; text: "待复核"; tone: "amber"; value: panel.count("review"); selected: panel.stateFilter === "review"; onClicked: panel.stateFilter = "review" }
                    FilterRow { objectName: "offlineStat-missing"; Layout.fillWidth: true; text: "未准备"; tone: "muted"; value: panel.count("missing"); selected: panel.stateFilter === "missing"; onClicked: panel.stateFilter = "missing" }
                    FilterRow { objectName: "offlineStat-processing"; Layout.fillWidth: true; text: "处理中"; tone: "blue"; value: panel.processingCount; caption: panel.unknownCount ? panel.unknownCount + " 需续查" : ""; selected: panel.stateFilter === "processing"; onClicked: panel.stateFilter = "processing" }
                    FilterRow { objectName: "offlineStat-failed"; Layout.fillWidth: true; text: "失败 / 需处理"; tone: "red"; value: panel.count("invalid") + Number(panel.batch.failed || 0); selected: panel.stateFilter === "failed"; onClicked: panel.stateFilter = "failed" }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.hairline; Layout.topMargin: 8; Layout.bottomMargin: 8 }
                    Text { objectName: "offlineReadySummary"; Layout.fillWidth: true; Layout.leftMargin: 4; Layout.rightMargin: 4; text: "离线就绪 " + panel.count("ready") + " / " + panel.count("total") + " 首"; color: Theme.text; font.pixelSize: 12; wrapMode: Text.Wrap }
                    Text { objectName: "offlineReadyNotice"; Layout.fillWidth: true; Layout.leftMargin: 4; Layout.rightMargin: 4; text: panel.count("missing") || panel.count("invalid") ? "还有歌曲没有歌词缓存，演出前在右边批量准备。离线演出时未就绪歌曲隐藏歌词。" : panel.count("total") ? "曲库已有本机歌词缓存；演出前检查待复核歌词。" : "先导入演出曲库，再批量准备歌词。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.3 }
                    Text { visible: !!panel.libraryData.indexingCount; Layout.fillWidth: true; Layout.leftMargin: 4; text: "正在核验音频：" + Number(panel.libraryData.indexingCount || 0) + " 首"; color: Theme.amber; font.pixelSize: 11 }
                    Item { Layout.fillHeight: true }
                    Text { Layout.fillWidth: true; Layout.leftMargin: 4; Layout.rightMargin: 4; text: "可以把音频或文件夹直接拖进来"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
                }
            }
            // 中：歌曲列表
            PanelCard {
                objectName: "libraryTrackColumn"
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; spacing: 0
                    Item {
                        visible: !panel.emptyLibrary
                        Layout.fillWidth: true; Layout.preferredHeight: 48
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 10; spacing: 10
                            Text { text: "歌曲"; color: Theme.text; font.pixelSize: Theme.fontSection; font.weight: Font.Medium }
                            Text { text: panel.filteredTracks.length + " 首"; color: Theme.muted; font.pixelSize: 12 }
                            Text { Layout.fillWidth: true; text: panel.libraryData.cacheDirectory || ""; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10; elide: Text.ElideMiddle; ToolTip.visible: dirHover.containsMouse; ToolTip.text: "本地歌词文件库：" + text; MouseArea { id: dirHover; anchors.fill: parent; acceptedButtons: Qt.NoButton; hoverEnabled: true } }
                            TextField {
                                id: searchField
                                objectName: "offlineLibrarySearch"
                                Layout.preferredWidth: panel.width < 1180 ? 170 : 230
                                implicitHeight: 32
                                leftPadding: 12
                                placeholderText: "搜索曲名 / 文件路径"
                                color: Theme.text
                                placeholderTextColor: Theme.muted
                                selectByMouse: true
                                font.pixelSize: 12
                                background: Rectangle { radius: height / 2; color: Theme.sunken; border.color: searchField.activeFocus ? Theme.focus : Theme.hairline; border.width: searchField.activeFocus ? 2 : 1 }
                            }
                        }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Theme.hairline }
                    }
                    Item {
                        Layout.fillWidth: true; Layout.fillHeight: true
                            ListView {
                                id: trackList
                                objectName: "offlineTrackList"
                                anchors.fill: parent; anchors.margins: 10
                                clip: true
                                spacing: 8
                                model: panel.filteredTracks
                                reuseItems: true
                                ScrollBar.vertical: ScrollBar {}
                                delegate: Rectangle {
                                    id: trackRow
                                    required property var modelData
                                    property bool moreOpen: false
                                    readonly property var preparationRow: panel.batchRow(modelData.versionId)
                                    readonly property bool demo: !!modelData.isDemo || String(modelData.versionId || "").indexOf("demo:") === 0
                                    readonly property bool canQuery: modelData.canQuery === undefined ? ["processing", "polling", "unknown"].indexOf(preparationRow.phase || "") >= 0 : !!modelData.canQuery
                                    readonly property bool canCorrect: modelData.canCorrect === undefined ? !demo && !!modelData.versionId && modelData.offlineState !== "invalid" && modelData.offlineState !== "changed" : !!modelData.canCorrect
                                    readonly property bool canExport: modelData.canExport === undefined ? !!modelData.offlineReady : !!modelData.canExport
                                    objectName: "offlineTrack-" + (modelData.versionId || "")
                                    width: trackList.width
                                    implicitHeight: trackContents.implicitHeight + 22
                                    height: implicitHeight
                                    color: Theme.surface
                                    radius: Theme.radiusCard
                                    border.color: Theme.hairline
                                    Rectangle { anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 1; width: 3; radius: 2; color: Theme.toneInk(panel.trackTone(trackRow.modelData)) }
                                    onModelDataChanged: moreOpen = false
                                    TapHandler { acceptedButtons: Qt.RightButton; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: trackRow.moreOpen = !trackRow.moreOpen }
                                    ColumnLayout {
                                        id: trackContents
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: 11; anchors.leftMargin: 16
                                        spacing: 5
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text { Layout.fillWidth: true; text: trackRow.modelData.title || "未命名曲目"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; elide: Text.ElideRight }
                                            Text { text: trackRow.modelData.durationText || ""; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10 }
                                            StatusPill { objectName: "offlineDuplicate-" + trackRow.modelData.versionId; visible: Number(trackRow.modelData.duplicateCount || 0) > 0; text: "可能重复 " + (Number(trackRow.modelData.duplicateCount || 0) + 1) + " 份"; tone: "amber"; ToolTip.visible: dupHover.containsMouse; ToolTip.text: "同名、时长差不到 1 秒的另一个文件。确认是同一首后，可只留一份。"; MouseArea { id: dupHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton } }
                            StatusPill { text: panel.trackLabel(trackRow.modelData); tone: panel.trackTone(trackRow.modelData) }
                                        }
                                        Text { Layout.fillWidth: true; text: trackRow.modelData.path || trackRow.modelData.versionId || ""; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10; elide: Text.ElideMiddle }
                                        Text { visible: !!trackRow.modelData.preparationDetail || !!trackRow.preparationRow.detail; Layout.fillWidth: true; text: trackRow.modelData.preparationDetail || trackRow.preparationRow.detail || ""; color: Theme.amber; font.pixelSize: 10; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
                                        Flow {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: childrenRect.height
                                            spacing: 6
                                            ActionButton { objectName: "offlineSetlist-" + trackRow.modelData.versionId; readonly property bool listed: panel.inSetlist(trackRow.modelData.versionId); text: listed ? "已在歌单" : "加入歌单"; tone: listed ? "neutral" : "primary"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: !trackRow.demo && !listed; onClicked: controller.addToSetlist(trackRow.modelData.versionId) }
                                            ActionButton { objectName: "offlineLoadA-" + trackRow.modelData.versionId; text: "回放 A"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: panel.replay; onClicked: controller.loadTrack(trackRow.modelData.versionId, "A") }
                                            ActionButton { objectName: "offlineLoadB-" + trackRow.modelData.versionId; text: "回放 B"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: panel.replay; onClicked: controller.loadTrack(trackRow.modelData.versionId, "B") }
                                            Item { width: 4; height: 1 }
                                            StudioButton { objectName: "offlineMore-" + trackRow.modelData.versionId; text: trackRow.moreOpen ? "收起" : "更多…"; implicitHeight: 28; font.pixelSize: 11; padding: 9; selected: trackRow.moreOpen; onClicked: trackRow.moreOpen = !trackRow.moreOpen; Accessible.name: "更多歌词操作" }
                                        }
                                        // 不常用的识别与导出操作收进这里，默认折叠，右键行或点「更多…」展开
                                        Rectangle {
                                            objectName: "offlineMorePanel-" + trackRow.modelData.versionId
                                            visible: trackRow.moreOpen
                                            Layout.fillWidth: true
                                            Layout.topMargin: 2
                                            Layout.preferredHeight: visible ? moreFlow.implicitHeight + 16 : 0
                                            radius: Theme.radiusControl
                                            color: Theme.sunken
                                            border.color: Theme.hairline
                                            Flow {
                                                id: moreFlow
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                                anchors.margins: 8
                                                spacing: 6
                                                ActionButton { objectName: "offlinePlan-" + trackRow.modelData.versionId; text: "审阅识别计划"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: !panel.offlineOnly && !trackRow.demo && !trackRow.modelData.offlineReady; onClicked: controller.planLibraryTrack(trackRow.modelData.versionId) }
                                                ActionButton { objectName: "offlineQuery-" + trackRow.modelData.versionId; text: "继续查询"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: !panel.offlineOnly && trackRow.canQuery; onClicked: controller.queryLibraryTrack(trackRow.modelData.versionId) }
                                                ActionButton { objectName: "offlineReference-" + trackRow.modelData.versionId; text: "参考纠错"; implicitHeight: 28; font.pixelSize: 11; padding: 9; enabled: trackRow.canCorrect; onClicked: controller.chooseLibraryReference(trackRow.modelData.versionId) }
                                                ActionButton { objectName: "offlineLrc-" + trackRow.modelData.versionId; text: "LRC"; implicitHeight: 28; implicitWidth: 48; font.pixelSize: 11; padding: 9; enabled: trackRow.canExport; onClicked: controller.exportLibraryLyrics(trackRow.modelData.versionId, "lrc") }
                                                ActionButton { objectName: "offlineSrt-" + trackRow.modelData.versionId; text: "SRT"; implicitHeight: 28; implicitWidth: 48; font.pixelSize: 11; padding: 9; enabled: trackRow.canExport; onClicked: controller.exportLibraryLyrics(trackRow.modelData.versionId, "srt") }
                                            }
                                        }
                                    }
                                }
                                Text { anchors.centerIn: parent; visible: panel.filteredTracks.length === 0 && !panel.emptyLibrary; text: "没有符合搜索的歌曲。"; color: Theme.secondary; font.pixelSize: 13 }
                                ImportCard {
                                    objectName: "offlineEmptyImport"
                                    visible: panel.emptyLibrary
                                    anchors.centerIn: parent
                                    width: Math.min(parent.width - 40, 460); height: 200
                                    text: "点击导入演出歌曲"; iconName: "audio"
                                    hint: "可以多选、导入整个文件夹，也可以直接拖进来。"
                                    note: "导入后在这里批量准备歌词，演出时离线跟随。"
                                    onClicked: controller.importAudioBatch()
                                }
                            }
                    }
                }
            }
            // 右：批量准备 / 今晚歌单
            PanelCard {
                objectName: "setlistPanel"
                visible: !panel.emptyLibrary
                Layout.preferredWidth: panel.setlistWidth; Layout.minimumWidth: panel.setlistWidth; Layout.maximumWidth: panel.setlistWidth
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; spacing: 0
                    Item {
                        Layout.fillWidth: true; Layout.preferredHeight: 44
                        RowLayout {
                            anchors.fill: parent; spacing: 0
                            TextTab { objectName: "libraryTab-batch"; Layout.fillWidth: true; text: "批量准备"; selected: panel.rightTab === 0; onClicked: panel.rightTab = 0 }
                            TextTab { objectName: "libraryTab-setlist"; Layout.fillWidth: true; text: Number(panel.setlist.total || 0) ? "今晚歌单 · " + Number(panel.setlist.total || 0) : "今晚歌单"; selected: panel.rightTab === 1; onClicked: panel.rightTab = 1 }
                        }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Theme.hairline }
                    }
                    StackLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: panel.rightTab
                        ColumnLayout {
                            objectName: "offlineBatchSection"
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 10; Layout.topMargin: 10; spacing: 6
                                StatusPill { text: panel.phaseLabel(panel.batch.status || "idle"); tone: ["running","planning","preflight","processing","polling"].indexOf(panel.batch.status || "") >= 0 ? "blue" : panel.batch.status === "failed" ? "red" : panel.batch.status === "unknown" || panel.batch.status === "blocked" || panel.batch.status === "paused" ? "amber" : panel.batch.status === "planned" ? "green" : "muted" }
                                Item { Layout.fillWidth: true }
                                StudioButton { objectName: "offlineCloudSettings"; text: "云识别设置"; implicitHeight: 26; font.pixelSize: 11; padding: 9; onClicked: panel.preparationRequested() }
                            }
                            ActionButton { objectName: "offlineBatchPlan"; Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; text: "1  生成计划"; implicitHeight: 34; font.pixelSize: 12; hint: "已缓存的歌曲直接复用，不重复提交"; enabled: !panel.offlineOnly && panel.count("total") > 0 && !panel.libraryData.indexingCount && !panel.libraryData.planning && ["running", "planning"].indexOf(panel.batch.status || "") < 0; onClicked: controller.planLibraryBatch() }
                            ActionButton { objectName: "offlineBatchReview"; Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; text: "2  审阅曲目"; implicitHeight: 34; font.pixelSize: 12; enabled: (panel.batch.rows || []).length > 0; onClicked: reviewDialog.open() }
                            RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; spacing: 6
                                ActionButton { objectName: "offlineBatchStart"; Layout.fillWidth: true; text: "3  启动准备"; tone: "primary"; implicitHeight: 34; font.pixelSize: 12; enabled: !!panel.batch.id && !!panel.batch.canStart && !panel.offlineOnly && !panel.libraryData.indexingCount && !panel.libraryData.planning; onClicked: controller.startLibraryBatch(panel.batch.id) }
                                ActionButton { objectName: "offlineBatchPause"; text: "暂停"; implicitHeight: 34; font.pixelSize: 12; enabled: ["running", "planning", "preflight", "processing"].indexOf(panel.batch.status || "") >= 0; onClicked: controller.pauseLibraryBatch() }
                            }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.hairline; Layout.topMargin: 4 }
                            Text { objectName: "offlineBatchSummary"; Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; text: panel.phaseLabel(panel.batch.status || "idle") + " · 共 " + Number(panel.batch.total || 0) + " 首 · 已缓存 " + Number(panel.batch.ready || 0) + " · 计划就绪 " + Number(panel.batch.planned || 0) + " · 待准备 " + Number(panel.batch.pending || 0) + " · 失败 " + Number(panel.batch.failed || 0) + "   预估新任务费用 " + Number(panel.batch.estimatedCost || panel.batch.estimatedCostPTC || 0).toFixed(4) + " " + (panel.batch.costUnit || (panel.batch.estimatedCostPTC !== undefined ? "PTC" : "元")); color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.3 }
                            Text { objectName: "offlineBatchBlockedReason"; Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; text: panel.offlineOnly ? "离线演出已启用，云端新提交和续查关闭。关闭后可继续准备曲库。" : panel.batch.blockedReason || panel.batch.detail || "先生成计划并审阅曲目；已有缓存直接复用，批量准备受云识别设置和本机额度约束。"; color: panel.offlineOnly || panel.batch.blockedReason ? Theme.amber : Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.3 }
                            Item { Layout.fillHeight: true }
                            Text { Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; Layout.bottomMargin: 10; text: "歌词库备份包含本地时间轴与版本索引，不复制原始音频；迁移后请核验实际音频版本。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
                        }
                        ColumnLayout {
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 8; Layout.topMargin: 8; spacing: 6
                                Text { objectName: "setlistCount"; Layout.fillWidth: true; text: Number(panel.setlist.total || 0) + " 首 · 已放 " + Number(panel.setlist.played || 0); color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight }
                                StudioButton { objectName: "setlistClearPlayed"; text: "清已放"; implicitHeight: 26; font.pixelSize: 11; padding: 9; visible: Number(panel.setlist.played || 0) > 0; onClicked: controller.clearSetlist(true) }
                                StudioButton { objectName: "setlistClear"; text: "清空"; hint: "清空歌单，曲库不动"; implicitHeight: 26; font.pixelSize: 11; padding: 9; visible: Number(panel.setlist.total || 0) > 0; onClicked: clearConfirm.open() }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 10; Layout.rightMargin: 10; Layout.bottomMargin: 10; spacing: 8
                                Text {
                                    objectName: "setlistReadiness"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 11
                                    readonly property var c: panel.setlist.counts || ({})
                                    text: !Number(panel.setlist.total || 0) ? "在左边点「加入歌单」，排好今晚要放的歌。" : Number(c.missing || 0) + Number(c.gone || 0) + Number(c.review || 0) === 0 ? "全部歌词就绪。" : "就绪 " + Number(c.ready || 0) + " · 待复核 " + Number(c.review || 0) + " · 没歌词 " + (Number(c.missing || 0) + Number(c.gone || 0)) + "，演出前补上。"
                                    color: !Number(panel.setlist.total || 0) ? Theme.muted : Number(c.missing || 0) + Number(c.gone || 0) ? Theme.amber : Number(c.review || 0) ? Theme.amber : Theme.green
                                }
                                ListView {
                                    id: setlistView
                                    objectName: "setlistList"
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    clip: true; spacing: 4
                                    model: panel.setlistItems
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: Rectangle {
                                        id: setRow
                                        required property var modelData
                                        objectName: "setlistRow-" + modelData.index
                                        width: setlistView.width; height: 44; radius: Theme.radiusControl
                                        color: setRow.modelData.onDeck ? Theme.accentSoft : rowHover.containsMouse ? Theme.hoverSurface : Theme.elevated
                                        opacity: setRow.modelData.played ? 0.55 : 1
                                        MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                                        RowLayout {
                                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 4; spacing: 6
                                            Text { text: String(setRow.modelData.index + 1); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 11; Layout.preferredWidth: 18; horizontalAlignment: Text.AlignRight }
                                            Rectangle { width: 7; height: 7; radius: 4; color: Theme.toneInk(panel.setlistTone(setRow.modelData.state)) }
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 0
                                                Text { Layout.fillWidth: true; text: setRow.modelData.title; color: Theme.text; font.pixelSize: 12; font.strikeout: setRow.modelData.played; elide: Text.ElideRight }
                                                Text { Layout.fillWidth: true; text: (setRow.modelData.onDeck ? "在盘上 · " : setRow.modelData.played ? "已放 · " : "") + setRow.modelData.label + (setRow.modelData.durationText ? " · " + setRow.modelData.durationText : ""); color: setRow.modelData.state === "ready" ? Theme.muted : Theme.amber; font.pixelSize: 10; elide: Text.ElideRight }
                                            }
                                            ToolButton { objectName: "setlistUp-" + setRow.modelData.index; visible: rowHover.containsMouse || activeFocus; enabled: setRow.modelData.index > 0; text: "↑"; implicitWidth: 22; implicitHeight: 24; Accessible.name: "上移"; onClicked: controller.moveSetlistItem(setRow.modelData.index, -1) }
                                            ToolButton { objectName: "setlistDown-" + setRow.modelData.index; visible: rowHover.containsMouse || activeFocus; enabled: setRow.modelData.index < panel.setlistItems.length - 1; text: "↓"; implicitWidth: 22; implicitHeight: 24; Accessible.name: "下移"; onClicked: controller.moveSetlistItem(setRow.modelData.index, 1) }
                                            ToolButton { objectName: "setlistPlayed-" + setRow.modelData.index; visible: rowHover.containsMouse || activeFocus; text: setRow.modelData.played ? "↺" : "✓"; implicitWidth: 22; implicitHeight: 24; Accessible.name: setRow.modelData.played ? "标为没放" : "标为已放"; ToolTip.visible: hovered; ToolTip.text: Accessible.name; onClicked: controller.setSetlistPlayed(setRow.modelData.index, !setRow.modelData.played) }
                                            ToolButton { objectName: "setlistRemove-" + setRow.modelData.index; visible: rowHover.containsMouse || activeFocus; text: "×"; implicitWidth: 22; implicitHeight: 24; Accessible.name: "移出歌单"; onClicked: controller.removeFromSetlist(setRow.modelData.index) }
                                        }
                                    }
                                    Text { anchors.centerIn: parent; visible: panel.setlistItems.length === 0; text: "歌单还是空的"; color: Theme.muted; font.pixelSize: 12 }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    visible: panel.replay && !!panel.setlist.next
                                    Text { Layout.fillWidth: true; text: "下一首放进回放"; color: Theme.muted; font.pixelSize: 11 }
                                    StudioButton { objectName: "setlistLoadA"; text: "A 盘"; implicitHeight: 26; onClicked: controller.loadSetlistNext("A") }
                                    StudioButton { objectName: "setlistLoadB"; text: "B 盘"; implicitHeight: 26; onClicked: controller.loadSetlistNext("B") }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    DropArea {
        id: dropZone
        objectName: "libraryDropArea"
        anchors.fill: parent
        keys: ["text/uri-list"]
        onDropped: function(drop) { if (drop.hasUrls) { controller.importDroppedPaths(drop.urls); drop.accept(); } }
    }
    Rectangle {
        objectName: "libraryDropHint"
        anchors.fill: parent; anchors.margins: 12
        visible: dropZone.containsDrag
        radius: Theme.radiusCard; color: Theme.accentSoft; opacity: 0.92
        border.color: Theme.accent; border.width: 2
        Column {
            anchors.centerIn: parent; spacing: 6
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "松手导入"; color: Theme.text; font.pixelSize: 22; font.weight: Font.Medium }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "音频和文件夹都可以，子文件夹也会找"; color: Theme.secondary; font.pixelSize: 13 }
        }
    }
    Dialog {
        id: reviewDialog
        objectName: "offlineBatchReviewDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(920, parent ? parent.width - 48 : 920)
        height: Math.min(620, parent ? parent.height - 48 : 620)
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        padding: 18
        background: Rectangle { radius: 14; color: Theme.surface; border.color: Theme.strokeStrong }
        contentItem: ColumnLayout {
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Text { Layout.fillWidth: true; text: "审阅批量歌词计划"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                ActionButton { objectName: "offlineBatchReviewClose"; text: "关闭"; implicitHeight: 32; onClicked: reviewDialog.close() }
            }
            Text { Layout.fillWidth: true; text: "共 " + Number(panel.batch.total || 0) + " 首 · 已缓存 " + Number(panel.batch.ready || 0) + " · 新任务预估 " + Number(panel.batch.estimatedCost || panel.batch.estimatedCostPTC || 0).toFixed(4) + " " + (panel.batch.costUnit || (panel.batch.estimatedCostPTC !== undefined ? "PTC" : "元")) + "。上传源为各曲实际音频文件，已缓存曲目直接复用。"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: (panel.stateData.preparation || {}).config ? ((panel.stateData.preparation || {}).config.summary || "") : "服务、路线与本机额度请在云识别设置中核验。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
            Text { visible: !!panel.batch.blockedReason; Layout.fillWidth: true; text: panel.batch.blockedReason || ""; color: Theme.amber; font.pixelSize: 11; wrapMode: Text.Wrap }
            ListView {
                id: reviewList
                objectName: "offlineBatchReviewList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: panel.batch.rows || []
                clip: true
                spacing: 8
                reuseItems: true
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: reviewRow
                    required property var modelData
                    width: reviewList.width
                    height: reviewContents.implicitHeight + 20
                    color: Theme.elevated
                    radius: Theme.radiusCard
                    ColumnLayout {
                        id: reviewContents
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 10
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: reviewRow.modelData.title || "未命名曲目"; color: Theme.text; font.pixelSize: 13; wrapMode: Text.Wrap }
                            Text { text: Number(reviewRow.modelData.estimatedCost || reviewRow.modelData.estimatedCostPTC || 0).toFixed(4) + " " + (panel.batch.costUnit || (panel.batch.estimatedCostPTC !== undefined ? "PTC" : "元")); color: Theme.amber; font.pixelSize: 11 }
                            StatusPill { text: panel.phaseLabel(reviewRow.modelData.phase); tone: reviewRow.modelData.phase === "ready" ? "green" : "amber" }
                        }
                        Text { Layout.fillWidth: true; text: "上传源  " + (reviewRow.modelData.path || "未核验路径"); color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.WrapAnywhere }
                        Text { Layout.fillWidth: true; text: "版本  " + (reviewRow.modelData.versionId || "未核验版本"); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 9; wrapMode: Text.WrapAnywhere }
                        Text { visible: !!reviewRow.modelData.detail; Layout.fillWidth: true; text: reviewRow.modelData.detail || ""; color: Theme.amber; font.pixelSize: 11; wrapMode: Text.Wrap }
                    }
                }
            }
            Text { Layout.fillWidth: true; text: "关闭审阅后可启动本次计划。暂停仅停止启动后续任务，已经受理的云任务保留。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
        }
    }
    Dialog {
        id: clearConfirm
        objectName: "setlistClearConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true; padding: 18; width: 340
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { radius: Theme.radiusCard; color: Theme.elevated; border.color: Theme.strokeStrong }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "清空今晚歌单？"; color: Theme.text; font.pixelSize: 16; font.weight: Font.Medium }
            Text { Layout.fillWidth: true; text: "只清歌单，曲库和歌词都不动。"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                StudioButton { text: "取消"; onClicked: clearConfirm.close() }
                ActionButton { objectName: "setlistClearConfirmButton"; text: "清空"; tone: "danger"; onClicked: { controller.clearSetlist(false); clearConfirm.close(); } }
            }
        }
    }
}
