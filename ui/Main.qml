import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Shapes

ApplicationWindow {
    id: app
    visible: true
    width: 1440; height: 900
    minimumWidth: 1000; minimumHeight: 700
    title: "包房战神 · " + (titles[currentPage] || "现场")
    color: Theme.canvas
    font.family: "PingFang SC"
    font.pixelSize: 13
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.accentText
    palette.windowText: Theme.text
    palette.buttonText: Theme.text
    palette.text: Theme.text
    palette.base: Theme.surface
    property var appState: controller ? controller.state : ({})
    property int currentPage: 0
    readonly property bool narrow: width < 1180
    readonly property bool replay: appState.inputMode ? appState.inputMode === "replay" : appState.mode === "replay"
    readonly property bool liveHidden: appState.selectedSource === "none"
    readonly property bool liveSourceReady: !appState.stale && (appState.effectiveSource === "A" || appState.effectiveSource === "B")
    readonly property string liveChipTone: appState.stale || liveHidden ? "red" : liveSourceReady ? "green" : "amber"
    readonly property string liveChipText: (appState.stale ? "状态过期" : liveHidden ? "歌词已隐藏" : liveSourceReady ? appState.effectiveSource + " 盘跟随中" : "来源未就绪") + " · " + (appState.outputOpen ? "客屏开" : "客屏关")
    readonly property var titles: ["现场", "曲库", "诊断与维护", "现场字幕特效", "歌曲准备", "制作工作台", "素材工具", "百炼与识别设置"]
    readonly property var subtitles: ["让每一句，都跟上现场。", "按实际音频版本准备歌词，演出时离线跟随。", "看清连接，才能放心上场。", "让客屏适合这个包房。", "为 A / B 盘当前歌曲准备歌词，结果审阅后再用。", "从一句歌词，到完整成片。", "整理素材，让画面更合适。"]
    function toggleLiveLyrics() { app.appState.selectedSource === "none" ? controller.restoreOutput() : controller.clearOutput(); }
    function openLiveEffects() { outputStyles.openEffects(); app.currentPage=3; }
    // Qt maps Control to Command on macOS; Meta keeps the physical Ctrl+H shown in the UI.
    Shortcut { sequence: Qt.platform.os === "osx" ? "Meta+H" : "Ctrl+H"; context: Qt.ApplicationShortcut; onActivated: app.toggleLiveLyrics() }
    Shortcut { sequence: Qt.platform.os === "osx" ? "Meta+Alt+K" : "Ctrl+Alt+K"; enabled: app.currentPage===5 && typeof studio!=="undefined" && !!studio; onActivated:studio.setSetting("lyricSweep",!studio.state.settings.lyricSweep) }
    Shortcut { sequence: "Ctrl+1"; onActivated: app.currentPage = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: app.currentPage = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: app.currentPage = 4 }
    Shortcut { sequence: "Ctrl+4"; onActivated: app.openLiveEffects() }
    Shortcut { sequence: "Ctrl+5"; onActivated: app.currentPage = 2 }
    Shortcut { sequence: "Ctrl+6"; onActivated: app.currentPage = 5 }
    Shortcut { sequence: "Ctrl+7"; onActivated: app.currentPage = 7 }

    component MenuEntry: MenuItem {
        id: entry
        property string caption: ""
        implicitHeight: caption ? 46 : 34
        contentItem: Column {
            spacing: 2
            Text { text: entry.text; color: Theme.text; font.pixelSize: 13 }
            Text { visible: !!entry.caption; text: entry.caption; color: Theme.muted; font.pixelSize: 11 }
        }
        background: Rectangle { radius: 6; color: entry.highlighted ? Theme.hoverSurface : "transparent" }
    }

    Menu {
        id: settingsMenu
        width: 240
        padding: 6
        background: Rectangle { radius: Theme.radiusCard; color: Theme.elevated; border.color: Theme.strokeStrong }
        MenuEntry { objectName: "nav-4"; text: "歌曲准备"; caption: "A / B 盘当前歌曲识别与续查"; onTriggered: app.currentPage=4 }
        MenuEntry { objectName: "nav-3"; text: "现场字幕特效"; caption: "样式库、入场与退场"; onTriggered: app.openLiveEffects() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.stroke } }
        MenuEntry { objectName: "nav-7"; text: "百炼与识别设置"; caption: "一个 Key，单首与批量共用"; onTriggered: app.currentPage=7 }
        MenuEntry { objectName: "nav-2"; text: "连接检查与诊断"; onTriggered: app.currentPage=2 }
        MenuEntry { objectName: "nav-6"; text: "素材工具"; onTriggered: app.currentPage=6 }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.stroke } }
        MenuEntry { objectName: "nav-preshow"; text: "开场自检"; caption: "开演前一眼看全"; onTriggered: preshowDialog.open() }
        MenuEntry { objectName: "nav-showlog"; text: "演出记录"; caption: "每晚放了什么、哪里手动接管"; onTriggered: { controller.refreshShowLog(""); showLogDialog.open(); } }
        MenuEntry { objectName: "nav-midi"; text: "MIDI 控制器"; caption: "打碟机空键当快捷键"; onTriggered: { controller.midiRefresh(); midiDialog.open(); } }
        MenuEntry { objectName: "nav-move"; text: "搬家包"; caption: "换电脑：歌词、设置、歌单一起带走"; onTriggered: moveDialog.open() }
    }

    component DialogFrame: Rectangle { radius: Theme.radiusCard; color: Theme.elevated; border.color: Theme.strokeStrong }

    Dialog {
        id: preshowDialog
        objectName: "preshowDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620, app.width - 48); height: Math.min(600, app.height - 48)
        modal: true; padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: DialogFrame {}
        // 内容变了才换清单：每秒重建行会让按钮点不中
        property var report: ({ rows: [], summary: "" })
        property string reportKey: ""
        function refresh() {
            var next = controller.preshowReport();
            var key = JSON.stringify(next);
            if (key !== reportKey) { reportKey = key; report = next; }
        }
        function toneColor(tone) { return tone === "green" ? Theme.green : tone === "red" ? Theme.red : tone === "amber" ? Theme.amber : Theme.muted; }
        function act(action) {
            if (action === "openOutput") controller.openStandaloneOutput();
            else if (action === "retryOutput") controller.openOutput();
            else if (action === "restore") controller.restoreOutput();
            else if (action === "offline") controller.setOfflineOnly(true);
            else if (action === "connect") {
                var mode = app.appState.mode;
                if (mode === "rekordbox_ax") controller.connectRekordboxAX();
                else if (mode === "rekordbox") controller.connectRekordbox();
                else controller.connectSerato();
            } else {
                preshowDialog.close();
                if (action === "screens") { app.currentPage = 0; livePanel.openScreenSettings(); }
                else if (action === "routing") app.currentPage = 0;
                else if (action === "wish") { app.currentPage = 0; livePanel.openGuestWishes(); }
                else if (action === "midi") { controller.midiRefresh(); midiDialog.open(); }
                else if (action === "library") app.currentPage = 1;
                else if (action === "prepare") app.currentPage = 4;
                return;
            }
            refresh();
        }
        onAboutToShow: { controller.midiRefresh(); controller.refreshOutputScreens(); refresh(); }
        Timer { interval: 1000; repeat: true; running: preshowDialog.visible; onTriggered: preshowDialog.refresh() }
        contentItem: ColumnLayout {
            spacing: 12
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { text: "开场自检"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium; Accessible.role: Accessible.Heading; Accessible.name: text }
                    Text { objectName: "preshowSummary"; Layout.fillWidth: true; text: preshowDialog.report.summary || ""; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap; Accessible.role: Accessible.StaticText; Accessible.name: text }
                }
                StudioButton { objectName: "preshowDone"; text: "完成"; tone: "primary"; onClicked: preshowDialog.close() }
            }
            ListView {
                objectName: "preshowList"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                model: preshowDialog.report.rows || []
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    id: preshowRow
                    required property var modelData
                    objectName: "preshowRow-" + modelData.id
                    width: ListView.view.width; height: Math.max(46, rowDetail.implicitHeight + 32)
                    Accessible.role: Accessible.StaticText
                    Accessible.name: modelData.label + "：" + modelData.detail
                    RowLayout {
                        anchors.fill: parent; anchors.rightMargin: 4; spacing: 12
                        Rectangle { objectName: "preshowDot-" + preshowRow.modelData.id; Layout.alignment: Qt.AlignTop; Layout.topMargin: 12; implicitWidth: 8; implicitHeight: 8; radius: 4; color: preshowDialog.toneColor(preshowRow.modelData.tone) }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6; spacing: 2
                            Text { text: preshowRow.modelData.label; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { id: rowDetail; objectName: "preshowDetail-" + preshowRow.modelData.id; Layout.fillWidth: true; text: preshowRow.modelData.detail; textFormat: Text.PlainText; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
                        }
                        StudioButton { objectName: "preshowAction-" + preshowRow.modelData.id; visible: !!preshowRow.modelData.action; text: preshowRow.modelData.actionLabel || ""; implicitHeight: 28; Layout.alignment: Qt.AlignVCenter; onClicked: preshowDialog.act(preshowRow.modelData.action) }
                    }
                }
            }
            Text { Layout.fillWidth: true; text: "只看这台电脑上的状态，不连网、不上传。客屏打开不代表电视有画面，开场前看一眼电视。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
        }
    }

    Dialog {
        id: showLogDialog
        objectName: "showLogDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(720, app.width - 48); height: Math.min(560, app.height - 48)
        modal: true; padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: DialogFrame {}
        readonly property var log: app.appState.showLog || ({})
        readonly property var summary: log.summary || ({})
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Text { Layout.fillWidth: true; text: "演出记录"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                DarkComboBox {
                    objectName: "showLogDay"; Layout.preferredWidth: 150; implicitHeight: 32
                    model: showLogDialog.log.days || []
                    currentIndex: Math.max(0, (showLogDialog.log.days || []).indexOf(showLogDialog.log.day))
                    onActivated: function(index) { controller.refreshShowLog((showLogDialog.log.days || [])[index]); }
                }
                StudioButton { objectName: "showLogExport"; text: "导出表格"; enabled: !!showLogDialog.log.day; onClicked: controller.exportShowLog(showLogDialog.log.day) }
                StudioButton { text: "关闭"; tone: "primary"; onClicked: showLogDialog.close() }
            }
            Text {
                objectName: "showLogSummary"; Layout.fillWidth: true; color: Theme.secondary; font.pixelSize: 12
                text: showLogDialog.log.day ? "上屏 " + Number(showLogDialog.summary.songs || 0) + " 首 · 手动换来源 " + Number(showLogDialog.summary.manual || 0) + " 次 · 隐藏 " + Number(showLogDialog.summary.hides || 0) + " 次 · 打拍 " + Number(showLogDialog.summary.taps || 0) + " 次 · 插字 " + Number(showLogDialog.summary.messages || 0) + " 条" : "还没有记录。现场上屏、换盘、隐藏、打拍、插字都会自动记下。"
            }
            ListView {
                objectName: "showLogList"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                model: showLogDialog.log.rows || []
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    required property var modelData
                    width: ListView.view.width; height: 26; spacing: 12
                    Text { text: modelData.time || ""; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 11; Layout.preferredWidth: 64 }
                    Text { text: modelData.label || ""; color: modelData.kind === "song" ? Theme.accent : modelData.kind === "hide" ? Theme.red : Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 72 }
                    Text { Layout.fillWidth: true; text: modelData.detail || ""; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 12; elide: Text.ElideRight }
                }
            }
        }
    }

    Dialog {
        id: midiDialog
        objectName: "midiDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(560, app.width - 48); height: Math.min(560, app.height - 48)
        modal: true; padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: DialogFrame {}
        readonly property var midi: app.appState.midi || ({})
        onClosed: if (midi.learning) controller.midiLearn("")
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Text { Layout.fillWidth: true; text: "MIDI 控制器"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                StatusPill { text: midiDialog.midi.connected ? "已连接" : "未连接"; tone: midiDialog.midi.connected ? "green" : "muted" }
            }
            Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.secondary; font.pixelSize: 12
                text: midiDialog.midi.available === false ? "MIDI 组件没装好，请重新安装依赖。" : "选打碟机，点「学习」，再按一下打碟机上没用的键。只读按键，不往打碟机发任何东西。" }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                DarkComboBox {
                    objectName: "midiPortCombo"; Layout.fillWidth: true; implicitHeight: 32
                    readonly property var ports: midiDialog.midi.ports || []
                    model: ["不使用"].concat(ports)
                    currentIndex: Math.max(0, ports.indexOf(midiDialog.midi.port) + 1)
                    onActivated: function(index) { controller.midiSelectPort(index === 0 ? "" : ports[index - 1]); }
                }
                StudioButton { objectName: "midiRefresh"; text: "刷新"; onClicked: controller.midiRefresh() }
            }
            Text { visible: !!midiDialog.midi.error; Layout.fillWidth: true; text: midiDialog.midi.error || ""; color: Theme.amber; font.pixelSize: 11; wrapMode: Text.Wrap }
            Text { visible: (midiDialog.midi.ports || []).length === 0; Layout.fillWidth: true; text: "没找到 MIDI 设备。插上打碟机后点「刷新」。"; color: Theme.muted; font.pixelSize: 12 }
            ListView {
                objectName: "midiMappingList"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
                model: midiDialog.midi.rows || []
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 42; radius: Theme.radiusControl
                    color: midiDialog.midi.learning === modelData.action ? Theme.accentSoft : Theme.surface
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 6; spacing: 8
                        Text { Layout.preferredWidth: 140; text: modelData.label; color: Theme.text; font.pixelSize: 13 }
                        Text { Layout.fillWidth: true; text: midiDialog.midi.learning === modelData.action ? "请按打碟机上的键…" : modelData.binding; color: midiDialog.midi.learning === modelData.action ? Theme.accent : modelData.key ? Theme.secondary : Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
                        StudioButton { objectName: "midiLearn-" + modelData.action; text: midiDialog.midi.learning === modelData.action ? "取消" : "学习"; implicitHeight: 28; enabled: !!midiDialog.midi.connected; onClicked: controller.midiLearn(midiDialog.midi.learning === modelData.action ? "" : modelData.action) }
                        StudioButton { objectName: "midiClear-" + modelData.action; text: "清除"; implicitHeight: 28; visible: !!modelData.key; onClicked: controller.midiClear(modelData.action) }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                StudioButton { text: "完成"; tone: "primary"; onClicked: midiDialog.close() }
            }
        }
    }

    Dialog {
        id: moveDialog
        objectName: "moveDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(480, app.width - 48)
        modal: true; padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: DialogFrame {}
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "搬家包"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium }
            Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.secondary; font.pixelSize: 12
                text: "带走：歌词库、字幕样式、每首歌的同步微调、今晚歌单、MIDI 按键、演出记录。\n不带：音乐文件、百炼 Key（在钥匙串里）、制作工程。\n新电脑导入后，到曲库导入同样的音乐，再点「核验曲库」。" }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "moveExport"; Layout.fillWidth: true; text: "导出搬家包"; tone: "primary"; onClicked: { moveDialog.close(); controller.exportMovePack(); } }
                ActionButton { objectName: "moveImport"; Layout.fillWidth: true; text: "导入搬家包"; onClicked: { moveDialog.close(); controller.importMovePack(); } }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                StudioButton { text: "关闭"; onClicked: moveDialog.close() }
            }
        }
    }

    // Zenox 式背景辉光：右上紫、左下蓝，很淡，只在卡片缝隙里露出来
    Item {
        objectName: "ambientGlow"
        anchors.fill: parent
        visible: !Theme.reduceTransparency
        opacity: 0.22
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: app.width * 0.82; centerY: -app.height * 0.05; centerRadius: app.width * 0.45
                    focalX: centerX; focalY: centerY
                    GradientStop { position: 0; color: "#bd53fb" }
                    GradientStop { position: 1; color: "transparent" }
                }
                startX: 0; startY: 0
                PathLine { x: app.width; y: 0 }
                PathLine { x: app.width; y: app.height }
                PathLine { x: 0; y: app.height }
                PathLine { x: 0; y: 0 }
            }
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: app.width * 0.08; centerY: app.height * 1.05; centerRadius: app.width * 0.4
                    focalX: centerX; focalY: centerY
                    GradientStop { position: 0; color: "#267ada" }
                    GradientStop { position: 1; color: "transparent" }
                }
                startX: 0; startY: 0
                PathLine { x: app.width; y: 0 }
                PathLine { x: app.width; y: app.height }
                PathLine { x: 0; y: app.height }
                PathLine { x: 0; y: 0 }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            objectName: "globalNavigation"
            Layout.fillWidth: true; Layout.preferredHeight: Theme.navHeight
            color: "#b3000000"
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 14; spacing: 8
                AppIcon { Layout.preferredWidth: 24; Layout.preferredHeight: 24; name: "prepare"; ink: Theme.gradientStart }
                Text { text: "包房战神"; color: Theme.text; font.pixelSize: 17; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; Layout.rightMargin: app.narrow ? 6 : 18 }
                // Zenox 式胶囊导航：外层半透明胶囊，选中项浅白底 + 渐变短横条
                Rectangle {
                    objectName: "navCapsule"
                    Layout.preferredHeight: 40; Layout.preferredWidth: navRow.implicitWidth + 8
                    radius: height / 2; color: Theme.wash; border.color: Theme.hairline
                    RowLayout {
                        id: navRow
                        anchors.centerIn: parent; spacing: 2
                        Repeater {
                            model: [{page:0,label:"现场",icon:"stage",key:"Ctrl+1"},{page:5,label:"制作",icon:"studio",key:"Ctrl+6"},{page:1,label:"曲库",icon:"library",key:"Ctrl+2"}]
                            delegate: Button {
                                id: navTab
                                required property var modelData
                                readonly property bool selected: app.currentPage===modelData.page || (modelData.page===5 && app.currentPage===6)
                                objectName: "nav-"+modelData.page
                                text: modelData.label
                                implicitHeight: 32; implicitWidth: 86
                                hoverEnabled: true; activeFocusOnTab: true
                                Accessible.name: modelData.label
                                Accessible.description: modelData.key
                                ToolTip.visible: hovered; ToolTip.text: modelData.label + " · " + modelData.key; ToolTip.delay: 600
                                onClicked: app.currentPage=modelData.page
                                contentItem: Item {
                                    Row {
                                        anchors.centerIn: parent; spacing: 7
                                        AppIcon { width: 16; height: 16; anchors.verticalCenter: parent.verticalCenter; name: navTab.modelData.icon; ink: navTab.selected ? Theme.text : navTab.hovered ? Theme.text : Theme.muted }
                                        Text { anchors.verticalCenter: parent.verticalCenter; text: navTab.text; color: navTab.selected ? Theme.text : navTab.hovered ? Theme.text : Theme.secondary; font.pixelSize: 13; font.weight: navTab.selected ? Font.Medium : Font.Normal }
                                    }
                                    AccentBar { visible: navTab.selected; anchors.bottom: parent.bottom; anchors.bottomMargin: -3; anchors.horizontalCenter: parent.horizontalCenter; width: 22; height: 2 }
                                }
                                background: Rectangle {
                                    radius: height / 2
                                    color: navTab.selected ? Theme.washHover : navTab.hovered ? Theme.wash : "transparent"
                                    border.color: navTab.activeFocus ? Theme.focus : "transparent"; border.width: 2
                                }
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true }
                Button {
                    id: liveChip
                    objectName: "globalLiveStatus"
                    visible: app.currentPage !== 0
                    implicitHeight: 30
                    implicitWidth: chipText.implicitWidth + 34
                    hoverEnabled: true; activeFocusOnTab: true
                    Accessible.name: "现场状态：" + app.liveChipText + "，前往现场"
                    ToolTip.visible: hovered; ToolTip.text: "现场歌词来源与客屏状态 · 点击前往现场"
                    onClicked: app.currentPage = 0
                    contentItem: Item {
                        Rectangle { x: 2; anchors.verticalCenter: parent.verticalCenter; width: 7; height: 7; radius: 4; color: Theme.toneInk(app.liveChipTone) }
                        Text { id: chipText; x: 15; anchors.verticalCenter: parent.verticalCenter; text: app.liveChipText; color: Theme.toneInk(app.liveChipTone); font.pixelSize: 12; font.weight: Font.Medium }
                    }
                    background: Rectangle { radius: 15; color: liveChip.hovered ? Theme.hoverSurface : Theme.toneSoft(app.liveChipTone); border.color: liveChip.activeFocus ? Theme.focus : Theme.toneLine(app.liveChipTone); border.width: liveChip.activeFocus ? 2 : 1 }
                }
                ActionButton {
                    objectName: "globalLyricsToggle"; visible: app.currentPage !== 0
                    text: app.liveHidden ? "恢复现场歌词" : "隐藏现场歌词"
                    hint: "Ctrl+H，在各页面隐藏或恢复现场歌词；保留原来源，不影响制作预览"
                    tone: app.liveHidden ? "primary" : "danger"; implicitHeight: 32
                    onClicked: app.toggleLiveLyrics()
                }
                StudioButton { id:settingsButton; objectName: "appSettingsMenu"; text: app.narrow && app.currentPage !== 0 ? "设置" : "设置与帮助"; iconName: "tools"; selected: [2,3,4,6,7].indexOf(app.currentPage)>=0; hint: "歌曲准备、现场字幕、百炼设置与连接检查"; implicitHeight: 32; onClicked: settingsMenu.popup(settingsButton,Math.min(0,settingsButton.width-settingsMenu.width),settingsButton.height+6) }
                Text { visible: !app.narrow; text: "0.13"; color: Theme.muted; font.pixelSize: 11; Accessible.name: "版本 0.13 测试版" }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 0
            Item {
                objectName: "pageHeader"
                Layout.fillWidth: true; visible: (app.currentPage > 1 && app.currentPage < 5 && app.currentPage !== 3) || app.currentPage===6
                Layout.preferredHeight: 58
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: Theme.pagePadding; anchors.rightMargin: Theme.pagePadding; anchors.topMargin: 6; spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Text { text: app.titles[app.currentPage] || "现场"; color: Theme.text; font.pixelSize: Theme.fontPageTitle; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; Accessible.role: Accessible.Heading; Accessible.name: text }
                        Text { Layout.fillWidth: true; text: app.subtitles[app.currentPage] || ""; color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight }
                    }
                    Text { visible: app.currentPage === 2 || app.currentPage === 4; text: "输入"; color: Theme.muted; font.pixelSize: 12 }
                    DarkComboBox {
                        objectName: "inputModeCombo"
                        visible: app.currentPage === 2 || app.currentPage === 4
                        Layout.preferredWidth: 180
                        model: ["回放演示", "Serato 只读", "rekordbox 测试副本", "rekordbox 原版跟随"]
                        currentIndex: app.replay ? 0 : app.appState.inputMode === "rekordbox_ax" ? 3 : app.appState.inputMode === "rekordbox" ? 2 : 1
                        onActivated: function(index){controller.setInputMode(["replay","serato","rekordbox","rekordbox_ax"][index]);}
                    }
                    ActionButton { visible: app.currentPage === 6; text: "返回制作"; onClicked: app.currentPage = 5 }
                }
            }
            StackLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                currentIndex: app.currentPage
                LivePanel {
                    id: livePanel
                    stateData: app.appState
                    onPreshowRequested: preshowDialog.open()
                    onPreparationRequested: app.currentPage = 4
                    onStyleRequested: app.openLiveEffects()
                    onSetlistRequested: app.currentPage = 1
                    onDiagnosticsRequested: app.currentPage = 2
                }
                OfflineLibraryPanel { stateData: app.appState; onPreparationRequested: app.currentPage = 4 }
                DiagnosticsPanel { stateData: app.appState }
                OutputStylePanel { id:outputStyles; stateData: app.appState }
                RecognitionPanel { stateData: app.appState; onApiRequested:app.currentPage=7; onSpeechRequested:function(deck){apiPanel.kind="speech";app.currentPage=7;if(apiPanel.apiModel)apiPanel.apiModel.setScope(deck);} }
                StudioPanel { onToolsRequested: app.currentPage = 6; onLiveRequested: app.currentPage = 0; onApiRequested:function(kind){apiPanel.kind=kind;app.currentPage=7;if(apiPanel.apiModel)apiPanel.apiModel.setScope("studio");} }
                StudioToolsPanel {}
                APISettingsPanel { id:apiPanel }
            }
            Rectangle {
                visible: app.currentPage!==5
                Layout.fillWidth: true; Layout.preferredHeight: 26
                color: Theme.sunken
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.stroke }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: Theme.pagePadding; anchors.rightMargin: Theme.pagePadding
                    Rectangle { width: 6; height: 6; radius: 3; color: app.currentPage < 5 && app.appState.stale ? Theme.red : Theme.muted }
                    Text { text:app.currentPage===7 ? "模型配置与审阅" : app.currentPage >= 5 ? "本地制作工作空间" : app.appState.stale ? "状态过期，歌词已隐藏" : (app.replay ? "回放时钟" : app.appState.mode === "rekordbox_ax" ? "rekordbox 原版 · 辅助功能只读" : app.appState.mode === "rekordbox" ? "rekordbox 测试副本 · 只读" : "Serato 只读"); color: app.currentPage < 5 && app.appState.stale ? Theme.red : Theme.secondary; font.pixelSize: 11 }
                    Item { Layout.fillWidth: true }
                    Text { text: "Ctrl+H 隐藏 / 恢复现场歌词"; color: Theme.muted; font.pixelSize: 11 }
                }
            }
        }
    }
}
