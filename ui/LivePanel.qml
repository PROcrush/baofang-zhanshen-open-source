import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Item {
    id: live
    property var stateData: ({})
    signal preparationRequested()
    signal styleRequested()
    signal diagnosticsRequested()
    signal setlistRequested()
    signal preshowRequested()
    function openScreenSettings() { controller.refreshOutputScreens(); screenPopup.open(); }
    function openGuestWishes() { rightTab = 2; contentPanel.tab = 2; contentPanel.previewActive = false; }
    function openScreenContent() { rightTab = 2; }
    readonly property var setlist: stateData.setlist || ({})
    readonly property var setlistNext: setlist.next || null
    readonly property var guestMessage: stateData.guestMessage || ({})
    readonly property var guestQr: stateData.guestQr || ({})
    readonly property var guestInbox: stateData.guestInbox || []
    readonly property bool canTap: ready && !hiddenLyrics
    // 输入框有焦点时不抢单键
    readonly property bool typing: !!live.Window.window && !!live.Window.window.activeFocusItem && typeof live.Window.window.activeFocusItem.cursorPosition !== "undefined"
    readonly property bool replay: stateData.inputMode ? stateData.inputMode === "replay" : stateData.mode === "replay"
    readonly property bool rekordboxAx: stateData.inputMode === "rekordbox_ax" || stateData.mode === "rekordbox_ax"
    readonly property bool rekordbox: rekordboxAx || stateData.inputMode === "rekordbox" || stateData.mode === "rekordbox"
    readonly property var axState: stateData.rekordboxAX || ({})
    readonly property string djName: rekordbox ? "rekordbox" : "Serato"
    readonly property bool small: height < 740
    readonly property bool compact: width < 1180
    readonly property int railWidth: compact ? 230 : 270
    readonly property int controlWidth: compact ? 248 : 290
    // 歌暮式栏标题：粗标题 + 一行灰字，底部细线
    component ColumnHeader: Item {
        property string text: ""
        property string detail: ""
        implicitHeight: 28
        AccentBar { id: headerBar; anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; width: 14; height: 3 }
        Text { id: headerTitle; anchors.left: headerBar.right; anchors.leftMargin: 8; anchors.verticalCenter: parent.verticalCenter; text: parent.text; color: Theme.secondary; font.pixelSize: 12; font.weight: Font.Medium; font.letterSpacing: 1 }
        Text { anchors.left: headerTitle.right; anchors.leftMargin: 8; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: parent.detail; color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
    }
    readonly property bool hiddenLyrics: stateData.selectedSource === "none"
    readonly property var deckA: stateData.deckA || ({})
    readonly property var deckB: stateData.deckB || ({})
    readonly property var lyric: stateData.lyric || ({})
    readonly property var settings: stateData.style || ({})
    readonly property string positionDeckId: stateData.activeDeck || "A"
    readonly property string lyricDeckId: stateData.lyricAdjustmentDeck || (stateData.effectiveSource === "B" ? "B" : stateData.effectiveSource === "A" ? "A" : positionDeckId)
    readonly property var lyricDeck: lyricDeckId === "B" ? deckB : deckA
    readonly property var positionDeck: positionDeckId === "B" ? deckB : deckA
    readonly property bool connected: !stateData.stale
    readonly property bool awaitingTracks: rekordbox && !!stateData.readerConnected && !stateData.readerHasTracks && !connected
    readonly property bool ready: connected && !!lyricDeck.valid && lyricDeck.cacheState === "ready"
    readonly property bool needsReview: ready && !!lyricDeck.needsReview
    readonly property bool singleLine: settings.outputMode === "dynamic" || settings.lineMode === "single"
    readonly property bool traditional: !singleLine && settings.lineMode === "karaoke"
    readonly property bool missingLyrics: lyricDeck.cacheState === "missing" || String((stateData.selection || {}).reasonCode || "").indexOf("lyrics_") === 0
    readonly property bool sourceAvailable: ready && !stateData.outputBlocked && (stateData.effectiveSource === "A" || stateData.effectiveSource === "B")
    readonly property real duration: Number(positionDeck.durationMs || 0)
    readonly property int offset: Number(stateData.lyricOffsetMs || 0)
    readonly property real speed: typeof stateData.speed === "number" ? stateData.speed : (replay ? 1 : 0)
    readonly property real fader: typeof stateData.videoFader === "number" ? stateData.videoFader : -1
    readonly property var target: stateData.outputTarget || ({screenName:"", fullScreen:false})
    readonly property bool overlayOutput: stateData.outputDestination === "serato_overlay"
    readonly property var overlayStatus: stateData.overlayStatus || ({phase:"idle",detail:"尚未打开 Serato 字幕叠加"})
    readonly property var screens: stateData.outputScreens || []
    readonly property var screenLabels: ["当前显示器"].concat(screens.map(function(row){return row.label;}))
    readonly property int screenIndex: {
        if (!target.screenName) return 0;
        for (var i=0;i<screens.length;i++) if(screens[i].name===target.screenName)return i+1;
        return 0;
    }
    readonly property bool missingScreen: target.screenName.length>0 && screenIndex===0
    readonly property var outputRecovery: stateData.outputRecovery || ({phase:"idle", detail:""})
    readonly property bool outputWanted: !!stateData.outputOpen || !!stateData.outputRequested
    readonly property string outputTitle: outputRecovery.phase === "waiting" ? "输出正在重开" : outputRecovery.phase === "failed" ? "输出恢复失败" : !stateData.outputOpen ? "输出未开启" : overlayOutput ? (overlayStatus.phase === "attached" ? "Serato 字幕已叠加" : overlayStatus.phase === "error" ? "字幕叠加失败" : "等待 Serato 视频窗口") : target.fullScreen && !missingScreen ? "客屏全屏已请求" : "客屏窗口已打开"
    readonly property string outputDetail: !stateData.outputOpen ? (outputRecovery.detail || "当前仅本机预览") : overlayOutput ? (overlayStatus.detail || "请打开 Serato Video Output") : missingScreen ? "显示器已断开，已返回普通窗口" : outputRecovery.detail || (target.screenName ? "显示器：" + target.screenName : "本机独立输出窗口")
    readonly property string outputTone: outputRecovery.phase === "failed" ? "red" : outputRecovery.phase === "waiting" ? "amber" : !stateData.outputOpen ? "muted" : overlayOutput ? (overlayStatus.phase === "attached" ? "green" : overlayStatus.phase === "error" ? "red" : "amber") : missingScreen ? "amber" : "green"
    readonly property string upcomingText: hiddenLyrics ? "恢复歌词后继续跟随" : !connected ? "连接恢复后显示下一句" : !sourceAvailable ? "准备当前歌曲的歌词后显示" : lyric.nextText || "等待下一句歌词"
    readonly property string qualityText: lyric.sweepTiming==="sentence" && settings.sweepMode!=="sentence" ? "按句估算扫色" : lyric.quality==="word" ? "字级" : lyric.quality==="sentence" ? "句级" : ""
    readonly property string readinessTitle: hiddenLyrics ? "歌词已手动隐藏" : awaitingTracks ? "rekordbox 已连接，等待曲目" : !connected ? (replay ? "回放状态已中断" : (rekordboxAx ? "等待原版 rekordbox 跟随" : rekordbox ? "等待连接 rekordbox" : "等待连接 Serato Video")) : !sourceAvailable ? "当前歌词来源未就绪" : needsReview ? lyricDeckId + " 盘歌词待复核" : lyric.waiting ? "歌词已就绪，等待开唱" : lyric.visible ? "歌词正在跟随 " + lyricDeckId + " 盘" : "歌词已就绪，等待下一句"
    readonly property string liveError: /^(同步微调未保存|客屏设置未保存|歌曲同步设置已满|字幕布局未保存|歌词走带未保存)/.test(stateData.notice || "") ? stateData.notice : ""
    readonly property string readinessDetail: liveError || (hiddenLyrics ? "恢复后继续使用之前的来源；背景继续显示。" : awaitingTracks ? "请在 rekordbox 的 A / B 盘装入本地歌曲；核验后匹配对应版本的歌词。" : !connected ? (replay ? "恢复回放后继续体验，演示不播放声音。" : (stateData.bridgeStatus || "等待播放状态") + (rekordboxAx ? "；打开原版，授权辅助功能，并手动确认 A / B 盘歌曲。" : rekordbox ? "；请打开已准备的 7.2.19 测试副本。" : "；请确认 Serato 已打开 Video 面板，可点击连接重试。")) : !sourceAvailable ? (stateData.selectionReason || (rekordboxAx ? "先确认当前盘实际歌曲，再指定 A / B 盘。" : "选择已准备歌词的盘，或前往歌曲准备。")) : needsReview ? "歌词可显示，文字尚需核对。点击“复核歌词”前往歌曲准备。" : replay ? "回放演示，无声音。自动跟随当前演示盘。" : rekordboxAx ? "已手动指定 " + lyricDeckId + " 盘；按界面源位置跟随，换歌后重新确认实际文件。" : stateData.selectedSource === "auto" ? (rekordbox ? "按内部混音推子选盘；THRU / 外部混音请指定 A / B。人声仍由 DJ 判断。" : "自动按视频推子跟随。人声混音需由 DJ 判断，可随时指定 A / B 盘。") : "已手动指定 " + lyricDeckId + " 盘。切歌后按实际音频版本匹配歌词。")

    readonly property bool axNeedsConfirm: rekordboxAx && connected && !sourceAvailable && !((axState.bindings || {})[lyricDeckId] || {}).versionId
    readonly property string bannerTone: hiddenLyrics || liveError ? "red" : !connected || !sourceAvailable || needsReview ? "amber" : "green"
    function toggleLyrics() { hiddenLyrics ? controller.restoreOutput() : controller.clearOutput(); }
    function sourceLabel() { return sourceAvailable ? lyricDeckId + " 盘" : "未就绪"; }
    Shortcut { sequence: "Ctrl+Shift+O"; enabled: live.visible; onActivated: live.outputWanted ? controller.closeOutput() : controller.openOutput() }
    Shortcut { sequence: "Ctrl+Alt+0"; enabled: live.visible && !live.rekordboxAx; onActivated: controller.selectSource("auto") }
    Shortcut { sequence: "Ctrl+Alt+1"; enabled: live.visible; onActivated: controller.selectSource("A") }
    Shortcut { sequence: "Ctrl+Alt+2"; enabled: live.visible; onActivated: controller.selectSource("B") }
    Shortcut { sequence: Qt.platform.os === "osx" ? "Meta+Alt+K" : "Ctrl+Alt+K"; enabled:live.visible; onActivated:controller.setLyricSweep(live.settings.sweepMode==="sentence") }
    // 现场单键：DJ 两手在打碟机上，一个键就够
    Shortcut { objectName:"liveTapShortcut"; sequence: "T"; enabled: live.visible && !live.typing; onActivated: controller.tapLyricSync() }
    Shortcut { sequence: "Shift+T"; enabled: live.visible && !live.typing && !!(stateData.lyricTap || {}).canUndo; onActivated: controller.undoLyricTap() }
    Shortcut { sequence: "["; enabled: live.visible && !live.typing && live.ready && live.offset>-3000; onActivated: controller.setLyricOffset(live.offset-100) }
    Shortcut { sequence: "]"; enabled: live.visible && !live.typing && live.ready && live.offset<3000; onActivated: controller.setLyricOffset(live.offset+100) }
    Shortcut { sequence: "\\"; enabled: live.visible && !live.typing && live.ready && live.offset!==0; onActivated: controller.setLyricOffset(0) }
    Shortcut { sequences: ["?", "F1"]; enabled: live.visible && !live.typing; onActivated: keyHelp.opened ? keyHelp.close() : keyHelp.open() }

    // 歌暮式按键摆放：
    // 顶部一条工具栏（小图标 + 文字，全局动作），右端放主操作；
    // 三栏各自顶部是标签页；栏内主操作放在栏的顶部；
    // 播放控制在预览正下方居中，时间在左、输出状态在下一行右侧。
    property int leftTab: 0
    property int rightTab: 0
    component ToolLink: StudioButton { implicitHeight: 30; font.pixelSize: 12; padding: 9 }
    component IconTab: Button {
        id: iconTab
        property string iconName: ""
        property bool selected: false
        implicitHeight: 48
        hoverEnabled: true; activeFocusOnTab: true
        Accessible.name: text
        contentItem: Column {
            spacing: 3
            AppIcon { anchors.horizontalCenter: parent.horizontalCenter; width: 18; height: 18; name: iconTab.iconName; ink: iconTab.selected ? Theme.text : Theme.muted }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: iconTab.text; color: iconTab.selected ? Theme.text : Theme.secondary; font.pixelSize: 11; font.weight: iconTab.selected ? Font.Medium : Font.Normal }
        }
        background: Rectangle {
            color: iconTab.selected || iconTab.hovered ? Theme.wash : "transparent"
            border.color: iconTab.activeFocus ? Theme.focus : "transparent"; border.width: 2
            AccentBar { visible: iconTab.selected; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: 24; height: 2 }
        }
    }
    component TextTab: Button {
        id: textTab
        property bool selected: false
        implicitHeight: 40
        hoverEnabled: true; activeFocusOnTab: true
        Accessible.name: text
        contentItem: Text { text: textTab.text; color: textTab.selected || textTab.hovered ? Theme.text : Theme.secondary; font.pixelSize: 13; font.weight: textTab.selected ? Font.Medium : Font.Normal; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle {
            color: "transparent"; border.color: textTab.activeFocus ? Theme.focus : "transparent"; border.width: 2
            AccentBar { visible: textTab.selected; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: Math.min(40, parent.width - 12); height: 2 }
        }
    }
    component Panel: Rectangle { color: Theme.surface; radius: Theme.radiusCard; border.color: Theme.hairline; clip: true }
    component HeaderLine: Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Theme.hairline }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 10; anchors.topMargin: 6
        spacing: 8
        // ① 工具栏：输入源与连接在左，工具排开，客屏主操作在最右
        RowLayout {
            objectName: "liveToolbar"
            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 2
            DarkComboBox {
                objectName:"liveInputModeCombo"; Layout.preferredWidth:160; implicitHeight:30
                model:["回放体验 · 无声音", "Serato 现场跟随", "rekordbox 测试副本", "rekordbox 原版跟随"]
                currentIndex:live.replay ? 0 : live.rekordboxAx ? 3 : live.rekordbox ? 2 : 1
                onActivated:function(index){controller.setInputMode(["replay","serato","rekordbox","rekordbox_ax"][index]);}
            }
            ToolLink { objectName:"connectSeratoButton"; iconName:"import"; text:live.compact ? (live.connected && !live.replay ? "重连" : "连接") : live.connected && !live.replay ? "重新连接" : "连接 " + live.djName; hint:"连接 / 重新连接 " + live.djName; onClicked:live.rekordboxAx ? controller.connectRekordboxAX() : live.rekordbox ? controller.connectRekordbox() : controller.connectSerato() }
            ToolLink { objectName:"bridgeStopButton"; iconName:"close"; text:live.compact ? "停止" : "停止跟随"; hint:"停止跟随 DJ 播放状态"; visible:!live.replay; enabled:stateData.bridgeRunning!==false; onClicked:controller.stopBridge() }
            Rectangle { Layout.preferredWidth:1; Layout.preferredHeight:16; color:Theme.hairline; Layout.leftMargin:6; Layout.rightMargin:6 }
            ToolLink { objectName:"liveLayersButton"; iconName:"lyrics"; text:"客屏内容"; selected:live.rightTab===2; hint:"喊话、DJ 标识和扫码祝福"; onClicked:live.openScreenContent() }
            ToolLink { objectName:"liveKeyHelpButton"; iconName:"tools"; text:"快捷键"; hint:"查看现场快捷键 · ?"; visible:!live.compact; onClicked:keyHelp.open() }
            ToolLink { objectName:"livePreshowButton"; iconName:"check"; text:"开场自检"; hint:"开演前一眼看全：打碟机、客屏、歌单、MIDI"; onClicked:live.preshowRequested() }
            ToolLink { objectName:"liveDiagnosticsButton"; iconName:"correct"; text:"诊断"; hint:stateData.bridgeStatus || "查看连接详情"; onClicked:live.diagnosticsRequested() }
            ToolLink { id:screenButton; objectName:"liveScreenSettings"; iconName:"film"; text:"显示器"; onClicked:live.openScreenSettings() }
            Item { Layout.fillWidth:true }
            ToolLink { objectName:"liveOverlayButton"; visible:!live.rekordbox; text:live.compact ? (live.outputWanted && live.overlayOutput ? "关闭叠加" : "Serato 叠加") : live.outputWanted && live.overlayOutput ? "关闭 Serato 字幕" : "叠加到 Serato"; selected:live.overlayOutput && live.outputWanted; hint:"将透明字幕叠加到 Serato Video Output 窗口"; onClicked:live.outputWanted && live.overlayOutput ? controller.closeOutput() : controller.openSeratoOverlay() }
            ActionButton { objectName:"liveOutputButton"; implicitHeight:30; font.pixelSize:12; padding:12; text:live.outputWanted && !live.overlayOutput ? "关闭客屏" : live.compact ? "打开客屏" : "打开独立客屏"; tone:!live.overlayOutput && !live.outputWanted ? "primary" : "neutral"; selected:!live.overlayOutput && live.outputWanted; hint:"在独立窗口显示包房战神背景与字幕"; onClicked:live.outputWanted && !live.overlayOutput ? controller.closeOutput() : controller.openStandaloneOutput() }
        }
        RowLayout {
            visible: !!stateData.outputOpen && live.overlayOutput
            Layout.fillWidth: true; Layout.preferredHeight: 24; spacing: 8
            Rectangle { width: 6; height: 6; radius: 3; color: live.overlayStatus.phase === "attached" ? Theme.green : live.overlayStatus.phase === "error" ? Theme.red : Theme.amber }
            Text { objectName:"liveOverlayStatus"; Layout.fillWidth: true; text: live.overlayStatus.detail || "正在定位视频输出窗口"; color: live.overlayStatus.phase === "attached" ? Theme.green : Theme.secondary; font.pixelSize: 11; elide: Text.ElideRight }
            StudioButton { objectName:"liveOverlayStyle"; text:"字幕特效"; implicitHeight: 25; onClicked:live.styleRequested() }
        }
        // ② 三栏
        RowLayout {
            objectName:"liveThreeColumns"
            Layout.fillWidth:true; Layout.fillHeight:true; Layout.minimumHeight:280; spacing:8
            // 左：图标标签「打碟机 / 歌单」
            Panel {
                objectName:"monitorRail"; Layout.minimumWidth:live.railWidth; Layout.maximumWidth:live.railWidth; Layout.preferredWidth:live.railWidth; Layout.fillHeight:true
                ColumnLayout {
                    anchors.fill:parent; spacing:0
                    Item {
                        Layout.fillWidth:true; Layout.preferredHeight:48
                        RowLayout {
                            anchors.fill:parent; spacing:0
                            IconTab { objectName:"liveLeftTab-decks"; Layout.fillWidth:true; text:"打碟机"; iconName:"stage"; selected:live.leftTab===0; onClicked:live.leftTab=0 }
                            IconTab { objectName:"liveLeftTab-setlist"; Layout.fillWidth:true; text:Number(live.setlist.total||0) ? "歌单 " + Number(live.setlist.played||0) + "/" + Number(live.setlist.total||0) : "歌单"; iconName:"library"; selected:live.leftTab===1; onClicked:live.leftTab=1 }
                        }
                        HeaderLine {}
                    }
                    StackLayout {
                        Layout.fillWidth:true; Layout.fillHeight:true; currentIndex:live.leftTab
                        ColumnLayout {
                            spacing:8
                            RowLayout {
                                Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:10; Layout.topMargin:6
                                Text { text:live.rekordboxAx ? "原版 · 手动 A / B" : live.djName; color:Theme.muted; font.pixelSize:11 }
                                Item { Layout.fillWidth:true }
                                StudioButton { objectName:"rekordboxAXAccessButton"; visible:live.rekordboxAx; text:"辅助功能权限"; implicitHeight:24; font.pixelSize:11; padding:8; onClicked:controller.requestRekordboxAXAccess() }
                            }
                            DeckCard { Layout.leftMargin:8; Layout.rightMargin:8; Layout.fillWidth:true; Layout.fillHeight:true; Layout.minimumHeight:126; deckId:"A"; deck:live.deckA; active:stateData.effectiveSource==="A"; replay:live.replay; observedOnly:live.rekordboxAx; binding:(live.axState.bindings || {})["A"] || ({}); onLoadRequested:controller.setDeck("A"); onPreparationRequested:live.preparationRequested(); onConfirmRequested:axBinding.openFor("A") }
                            DeckCard { Layout.leftMargin:8; Layout.rightMargin:8; Layout.fillWidth:true; Layout.fillHeight:true; Layout.minimumHeight:126; deckId:"B"; deck:live.deckB; active:stateData.effectiveSource==="B"; replay:live.replay; observedOnly:live.rekordboxAx; binding:(live.axState.bindings || {})["B"] || ({}); onLoadRequested:controller.setDeck("B"); onPreparationRequested:live.preparationRequested(); onConfirmRequested:axBinding.openFor("B") }
                            ColumnLayout {
                                visible:!live.rekordboxAx; Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; Layout.preferredHeight:42; spacing:4
                                RowLayout {
                                    Layout.fillWidth:true
                                    Text { text:live.replay ? "演示视频推子" : live.rekordbox ? "混音推子 · 只读" : "视频推子 · 只读"; color:Theme.secondary; font.pixelSize:11 }
                                    Item { Layout.fillWidth:true }
                                    Text { text:live.fader>=0 ? Math.round(live.fader*100)+"%" : "尚未读取"; color:Theme.text; font.family: Theme.numberFont; font.pixelSize:11 }
                                }
                                VideoFader { objectName:"videoFaderMeter"; Layout.fillWidth:true; Layout.preferredHeight:24; value:live.fader; sourceLabel:live.rekordbox ? "混音推子" : "视频推子" }
                            }
                            Rectangle {
                                objectName:"liveSetlistCard"; Layout.fillWidth:true; Layout.preferredHeight:38
                                color:setlistHover.containsMouse ? Theme.wash : "transparent"
                                Accessible.role:Accessible.Button; Accessible.name:"今晚歌单，" + setlistLine.text
                                MouseArea { id:setlistHover; anchors.fill:parent; hoverEnabled:true; cursorShape:Qt.PointingHandCursor; onClicked:live.leftTab=1 }
                                Rectangle { anchors.top:parent.top; width:parent.width; height:1; color:Theme.hairline }
                                RowLayout {
                                    anchors.fill:parent; anchors.leftMargin:12; anchors.rightMargin:12; spacing:6
                                    Rectangle { visible:!!live.setlistNext; width:7; height:7; radius:4; color:Theme.toneInk(!live.setlistNext ? "muted" : live.setlistNext.state==="ready" ? "green" : live.setlistNext.state==="review" ? "amber" : "red") }
                                    Text { id:setlistLine; objectName:"liveSetlistNext"; Layout.fillWidth:true; text:live.setlistNext ? "下一首 · " + live.setlistNext.title : Number(live.setlist.total||0) ? "歌单已放完" : "到曲库排好今晚的歌"; color:live.setlistNext ? Theme.text : Theme.muted; font.pixelSize:12; elide:Text.ElideRight }
                                    Text { objectName:"liveSetlistProgress"; text:Number(live.setlist.total||0) ? Number(live.setlist.played||0) + " / " + Number(live.setlist.total||0) : ""; color:Theme.muted; font.pixelSize:11; font.family: Theme.numberFont }
                                }
                            }
                        }
                        ColumnLayout {
                            spacing:6
                            RowLayout {
                                Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:8; Layout.topMargin:8
                                Text { Layout.fillWidth:true; text:Number(live.setlist.total||0) ? "已放 " + Number(live.setlist.played||0) + " / " + Number(live.setlist.total||0) : "还没排歌"; color:Theme.muted; font.pixelSize:11 }
                                StudioButton { objectName:"liveSetlistEdit"; text:"去曲库排歌"; implicitHeight:26; font.pixelSize:11; padding:9; onClicked:live.setlistRequested() }
                            }
                            ListView {
                                objectName:"liveSetlistList"
                                Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:2
                                model:live.setlist.items || []
                                delegate: Rectangle {
                                    required property var modelData
                                    width:ListView.view.width; height:36
                                    color:modelData.onDeck ? Theme.wash : "transparent"
                                    opacity:modelData.played ? 0.5 : 1
                                    RowLayout {
                                        anchors.fill:parent; anchors.leftMargin:12; anchors.rightMargin:10; spacing:8
                                        Text { text:String(modelData.index+1); color:Theme.muted; font.family:Theme.numberFont; font.pixelSize:11; Layout.preferredWidth:16; horizontalAlignment:Text.AlignRight }
                                        Rectangle { width:6; height:6; radius:3; color:Theme.toneInk(modelData.state==="ready" ? "green" : modelData.state==="review" ? "amber" : "red") }
                                        Text { Layout.fillWidth:true; text:modelData.title; color:Theme.text; font.pixelSize:12; font.strikeout:modelData.played; elide:Text.ElideRight }
                                        Text { text:modelData.onDeck ? "在盘上" : modelData.played ? "已放" : modelData.state==="ready" ? "" : modelData.label; color:modelData.onDeck ? Theme.accent : Theme.muted; font.pixelSize:10 }
                                    }
                                }
                                Text { anchors.centerIn:parent; visible:(live.setlist.items || []).length===0; text:"在曲库点「加入歌单」"; color:Theme.muted; font.pixelSize:12 }
                            }
                        }
                    }
                }
            }
            // 中：顶部是现场状态，预览下面居中放播放控制
            Panel {
                objectName:"programPreviewFrame"; Layout.fillWidth:true; Layout.fillHeight:true; Layout.minimumWidth:320
                ColumnLayout {
                    anchors.fill:parent; spacing:0
                    Rectangle {
                        objectName:"liveReadinessBanner"
                        Layout.fillWidth:true; Layout.preferredHeight:48
                        color:live.bannerTone === "green" ? "transparent" : Theme.toneSoft(live.bannerTone)
                        HeaderLine {}
                        RowLayout {
                            anchors.fill:parent; anchors.leftMargin:14; anchors.rightMargin:8; spacing:10
                            Item {
                                width:10; height:10
                                Rectangle { anchors.centerIn:parent; width:18; height:18; radius:9; color:Theme.toneInk(live.bannerTone); opacity:0.18 }
                                Rectangle { anchors.fill:parent; radius:5; color:Theme.toneInk(live.bannerTone) }
                            }
                            Text { objectName:"liveReadinessTitle"; text:live.liveError ? "设置未保存，请检查" : live.readinessTitle; color:Theme.text; font.pixelSize:14; font.weight:Font.Medium; elide:Text.ElideRight; Layout.maximumWidth:parent.width*0.45; Accessible.role:Accessible.StaticText; Accessible.name:text }
                            Text {
                                objectName:"liveReadinessDetail"; Layout.fillWidth:true; text:live.readinessDetail; color:live.liveError ? Theme.red : Theme.muted; font.pixelSize:11; elide:Text.ElideRight
                                Accessible.role:Accessible.StaticText; Accessible.name:text
                                ToolTip.visible:readinessHover.containsMouse; ToolTip.text:text
                                MouseArea { id:readinessHover; anchors.fill:parent; acceptedButtons:Qt.NoButton; hoverEnabled:true }
                            }
                            ActionButton {
                                objectName:"liveNextAction"; visible:live.hiddenLyrics || !live.connected || !live.sourceAvailable || live.needsReview
                                text:live.hiddenLyrics ? "恢复歌词" : live.axNeedsConfirm ? "确认 " + live.lyricDeckId + " 盘歌曲" : !live.connected ? (live.replay ? "恢复回放" : "查看连接帮助") : live.sourceAvailable && live.needsReview ? "复核歌词" : live.ready ? "指定当前盘" : live.missingLyrics ? "准备这首歌" : "查看原因"
                                tone:"primary"; implicitHeight:30; font.pixelSize:12; padding:12
                                onClicked:{
                                    if(live.hiddenLyrics)controller.restoreOutput();
                                    else if(live.axNeedsConfirm)axBinding.openFor(live.lyricDeckId);
                                    else if(!live.connected)live.replay ? controller.reconnect() : live.diagnosticsRequested();
                                    else if(live.sourceAvailable && live.needsReview)live.preparationRequested();
                                    else if(live.ready)controller.selectSource(live.lyricDeckId);
                                    else if(live.missingLyrics)live.preparationRequested();
                                    else live.diagnosticsRequested();
                                }
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth:true; Layout.fillHeight:true; color:Theme.sunken
                        ColumnLayout {
                            anchors.fill:parent; spacing:0
                            RowLayout {
                                visible:live.rightTab===2 && contentPanel.previewAvailable
                                Layout.fillWidth:true; Layout.preferredHeight:28; Layout.leftMargin:10; Layout.rightMargin:10
                                TextTab { objectName:"screenPreviewLive"; text:"现场画面"; selected:!contentPanel.previewActive; onClicked:contentPanel.previewActive=false }
                                TextTab { objectName:"screenPreviewDraft"; text:"待上屏"; selected:contentPanel.previewActive; onClicked:contentPanel.previewActive=true }
                                Text { objectName:"screenPreviewLabel"; Layout.fillWidth:true; text:contentPanel.previewActive ? "仅本机可见 · 点击上屏才发布" : live.outputWanted ? "输出预览" : "客屏未开"; color:contentPanel.previewActive ? Theme.amber : Theme.muted; font.pixelSize:10; elide:Text.ElideRight }
                            }
                            Item {
                                Layout.fillWidth:true; Layout.fillHeight:true
                                ProgramView {
                                    id:liveProgramPreview; anchors.centerIn:parent; width:Math.min(parent.width-16,(parent.height-16)*16/9); height:width*9/16
                                    stateData:live.rightTab===2 && contentPanel.previewAvailable && contentPanel.previewActive ? contentPanel.previewState : live.stateData
                                    compact:true; transparentStage:live.overlayOutput; radius:6
                                    Rectangle {
                                        objectName:"screenLyricSafeGuide"; visible:live.rightTab===2 && contentPanel.previewAvailable && contentPanel.previewActive
                                        x:parent.width*.045; y:Math.max(0,Math.min(.7,(Number(live.settings.verticalPosition || 56)/100)-.1))*parent.height
                                        width:parent.width*.91; height:parent.height*.26; color:"transparent"; border.color:"#667E718C"; radius:4
                                        Text { anchors.right:parent.right; anchors.bottom:parent.bottom; anchors.margins:4; text:"歌词区域 · 注意避让"; color:Theme.muted; font.pixelSize:9 }
                                    }
                                }
                            }
                        }
                    }
                    ControlSlider {
                        id:playhead; objectName:"playheadSlider"; Layout.fillWidth:true; Layout.preferredHeight:18; Layout.leftMargin:10; Layout.rightMargin:10; Layout.topMargin:4
                        visible:live.duration>0; from:0; to:Math.max(1,live.duration/1000); value:Number(live.positionDeck.positionMs||0)/1000
                        enabled:live.replay && live.connected
                        Accessible.name:live.replay ? "回放播放位置" : "只读源播放位置"
                        onMoved:controller.seek(value)
                    }
                    // 播放条：时间在左，播放键居中，速度在右（同歌暮播放器）
                    Item {
                        Layout.fillWidth:true; Layout.preferredHeight:40
                        RowLayout {
                            anchors.left:parent.left; anchors.leftMargin:14; anchors.verticalCenter:parent.verticalCenter; spacing:6
                            Text { objectName:"sourcePositionTitle"; text:live.positionDeckId + " 盘" + (live.replay ? " · 回放" : " · 只读"); color:Theme.muted; font.pixelSize:11 }
                            Text { text:live.positionDeck.positionText || "--:--.---"; color:Theme.accent; font.family: Theme.numberFont; font.pixelSize:12 }
                            Text { objectName:"unknownDurationNotice"; visible:live.duration<=0; text:"总时长未解析"; color:Theme.muted; font.pixelSize:11 }
                        }
                        Row {
                            anchors.centerIn:parent; spacing:4
                            visible:live.replay
                            StudioButton { objectName:"replayRestartButton"; iconName:"previous"; iconOnly:true; text:"回到起点"; implicitHeight:30; enabled:live.replay && live.connected; onClicked:controller.seek(0) }
                            ActionButton { objectName:"playbackButton"; text:stateData.playing ? "暂停演示" : "播放演示"; tone:"primary"; implicitHeight:30; font.pixelSize:12; padding:12; enabled:live.replay && live.connected; onClicked:controller.togglePlayback() }
                            StudioButton { objectName:"loopButton"; text:"循环"; hint:"循环 8–16 秒"; implicitHeight:30; font.pixelSize:12; selected:!!stateData.loopEnabled; enabled:live.replay && live.connected; onClicked:controller.toggleLoop() }
                        }
                        Text { objectName:"speedValueText"; visible:!live.replay && !live.rekordboxAx; anchors.centerIn:parent; text:"速度 " + live.speed.toFixed(2)+"×"; color:Theme.secondary; font.family: Theme.numberFont; font.pixelSize:12 }
                        Text { objectName:"rekordboxAXPlaybackNotice"; visible:live.rekordboxAx; anchors.centerIn:parent; text:"播停与速度未提供"; color:Theme.muted; font.pixelSize:11 }
                        RowLayout {
                            anchors.right:parent.right; anchors.rightMargin:8; anchors.verticalCenter:parent.verticalCenter; spacing:6
                            visible:live.replay
                            ControlSlider { objectName:"speedSlider"; Layout.preferredWidth:live.compact ? 54 : 80; Layout.preferredHeight:22; from:0.8; to:1.2; stepSize:0.01; value:live.speed; enabled:live.replay && live.connected; Accessible.name:"回放速度"; onMoved:controller.setSpeed(value) }
                            Text { text:live.speed.toFixed(2)+"×"; color:Theme.secondary; font.family: Theme.numberFont; font.pixelSize:11 }
                            StudioButton { objectName:"disconnectButton"; text:stateData.stale ? "恢复" : "模拟中断"; implicitHeight:26; font.pixelSize:11; padding:8; hint:"只用于演示失联后的隐藏行为"; onClicked:stateData.stale ? controller.reconnect() : controller.simulateDisconnect() }
                        }
                    }
                    Rectangle { Layout.fillWidth:true; Layout.preferredHeight:1; color:Theme.hairline }
                    RowLayout {
                        Layout.fillWidth:true; Layout.preferredHeight:40; Layout.leftMargin:14; Layout.rightMargin:10; spacing:8
                        Text { text:"下一句"; color:Theme.muted; font.pixelSize:11 }
                        Text {
                            objectName:"liveUpcomingLyric"; Layout.fillWidth:true; text:live.upcomingText; color:live.sourceAvailable && !live.hiddenLyrics && live.lyric.nextText ? Theme.text : Theme.secondary; font.pixelSize:14; font.weight:Font.Medium; elide:Text.ElideRight
                            Accessible.role:Accessible.StaticText; Accessible.name:"下一句，"+text
                            ToolTip.visible:upcomingHover.containsMouse; ToolTip.text:text
                            MouseArea { id:upcomingHover; anchors.fill:parent; acceptedButtons:Qt.NoButton; hoverEnabled:true }
                        }
                        Text { visible:!live.compact; text:live.sourceAvailable && !live.hiddenLyrics ? live.lyricDeckId + " 盘" + (live.qualityText ? " · " + live.qualityText : "") : "歌词未显示"; color:Theme.muted; font.pixelSize:11 }
                        StatusPill { objectName:"liveOutputStatus"; text:live.outputTitle; tone:live.outputTone }
                        Text {
                            objectName:"liveOutputDetail"; visible:!live.compact; Layout.maximumWidth:150; text:live.outputDetail; color:Theme.muted; font.pixelSize:11; elide:Text.ElideRight
                            Accessible.role:Accessible.StaticText; Accessible.name:text
                            ToolTip.visible:outputDetailHover.containsMouse; ToolTip.text:text
                            MouseArea { id:outputDetailHover; anchors.fill:parent; acceptedButtons:Qt.NoButton; hoverEnabled:true }
                        }
                    }
                }
            }
            // 右：来源和隐藏常驻顶部；下面是「同步 / 样式 / 插字」标签
            Panel {
                objectName:"sourceControlBar"; Layout.preferredWidth:live.controlWidth; Layout.minimumWidth:live.controlWidth; Layout.maximumWidth:live.controlWidth; Layout.fillHeight:true
                ColumnLayout {
                    anchors.fill:parent; spacing:0
                    ColumnLayout {
                        Layout.fillWidth:true; Layout.margins:12; spacing:8
                        ColumnHeader { Layout.fillWidth:true; text:"歌词来源"; detail:live.hiddenLyrics ? "已暂时隐藏" : stateData.selectedSource==="auto" ? "自动选择" : "已人工接管" }
                        Rectangle {
                            Layout.fillWidth:true; Layout.preferredHeight:36; radius:height/2; color:Theme.wash; border.color:Theme.hairline
                            RowLayout {
                                anchors.fill:parent; anchors.margins:3; spacing:2
                                Repeater {
                                    model:[{key:"auto",label:"自动",hint:"按视频推子选盘，混合人声需人工判断 · Ctrl+Alt+0"},{key:"A",label:"A 盘",hint:"只显示 A 盘歌词 · Ctrl+Alt+1"},{key:"B",label:"B 盘",hint:"只显示 B 盘歌词 · Ctrl+Alt+2"}]
                                    delegate:StudioButton {
                                        required property var modelData
                                        Layout.fillWidth:true; Layout.fillHeight:true
                                        objectName:"source-"+modelData.key; text:modelData.label; enabled:!(live.rekordboxAx && modelData.key === "auto"); hint:modelData.key === "auto" && live.rekordboxAx ? "原版跟随本轮请手动指定 A / B 盘" : modelData.key === "auto" && live.rekordbox ? "按内部混音推子选盘；THRU、同侧分配和外部混音时请指定 A / B" : modelData.hint
                                        selected:stateData.selectedSource===modelData.key
                                        Accessible.name:modelData.key === "auto" ? "自动跟随" : "指定 " + modelData.label
                                        onClicked:controller.selectSource(modelData.key)
                                    }
                                }
                            }
                        }
                        ActionButton { objectName:"clearOutputButton"; Layout.fillWidth:true; text:live.hiddenLyrics ? "恢复歌词  Ctrl+H" : "隐藏歌词  Ctrl+H"; hint:live.hiddenLyrics ? "恢复之前的歌词来源，未就绪的歌曲继续隐藏" : "立即隐藏歌词，背景继续显示"; tone:live.hiddenLyrics ? "primary" : "danger"; implicitHeight:38; onClicked:live.toggleLyrics() }
                    }
                    Item {
                        Layout.fillWidth:true; Layout.preferredHeight:40
                        Rectangle { anchors.top:parent.top; width:parent.width; height:1; color:Theme.hairline }
                        RowLayout {
                            anchors.fill:parent; spacing:0
                            TextTab { objectName:"liveTab-sync"; Layout.fillWidth:true; text:"同步"; selected:live.rightTab===0; onClicked:live.rightTab=0 }
                            TextTab { objectName:"liveTab-style"; Layout.fillWidth:true; text:"客屏样式"; selected:live.rightTab===1; onClicked:live.rightTab=1 }
                            TextTab { objectName:"liveTab-message"; Layout.fillWidth:true; text:"客屏内容"; selected:live.rightTab===2; onClicked:live.rightTab=2 }
                        }
                        HeaderLine {}
                    }
                    StackLayout {
                        Layout.fillWidth:true; Layout.fillHeight:true; currentIndex:live.rightTab
                        ColumnLayout {
                            spacing:8
                            Text { Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; Layout.topMargin:10; text:live.lyricDeckId + " 盘 · 只存当前歌曲"; color:Theme.muted; font.pixelSize:11; elide:Text.ElideRight }
                            ActionButton {
                                objectName:"liveTapSync"; Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; text:"打拍对齐  T"; tone:"primary"; implicitHeight:38
                                enabled:live.canTap
                                hint:"听到一句刚开唱时按一下，整首歌词对齐到这一刻，不改歌词时码"
                                onClicked:controller.tapLyricSync()
                            }
                            RowLayout {
                                Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; spacing:4
                                StudioButton { objectName:"liveOffsetEarlier"; Layout.fillWidth:true; text:"提前 0.1s"; hint:"歌词比人声慢时使用 · [ 键"; enabled:live.ready && live.offset>-3000; implicitHeight:30; onClicked:controller.setLyricOffset(live.offset-100) }
                                Text { objectName:"liveOffsetValue"; Layout.preferredWidth:56; horizontalAlignment:Text.AlignHCenter; text:(live.offset>0 ? "+" : "")+live.offset+" ms"; color:live.offset===0 ? Theme.secondary : Theme.accent; font.family: Theme.numberFont; font.pixelSize:12 }
                                StudioButton { objectName:"liveOffsetLater"; Layout.fillWidth:true; text:"延后 0.1s"; hint:"歌词比人声快时使用 · ] 键"; enabled:live.ready && live.offset<3000; implicitHeight:30; onClicked:controller.setLyricOffset(live.offset+100) }
                            }
                            RowLayout {
                                Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; spacing:4
                                StudioButton { objectName:"liveTapUndo"; text:"撤销打拍"; hint:"撤销上一次打拍 · Shift+T"; enabled:!!(stateData.lyricTap || {}).canUndo; implicitHeight:28; font.pixelSize:12; onClicked:controller.undoLyricTap() }
                                Item { Layout.fillWidth:true }
                                StudioButton { objectName:"liveOffsetReset"; text:"归零"; hint:"同步微调归零 · \\ 键"; enabled:live.ready && live.offset!==0; implicitHeight:28; font.pixelSize:12; onClicked:controller.setLyricOffset(0) }
                            }
                            Item { Layout.fillHeight:true }
                        }
                        ColumnLayout {
                            spacing:8
                            GridLayout {
                                Layout.fillWidth:true; Layout.leftMargin:12; Layout.rightMargin:12; Layout.topMargin:10; columns:2; rowSpacing:6; columnSpacing:6
                                StudioButton { objectName:"liveQuickKaraoke"; Layout.fillWidth:true; text:"传统 KTV"; selected:live.traditional; implicitHeight:32; onClicked:controller.setLiveLayout("karaoke") }
                                StudioButton { objectName:"liveQuickStandard"; Layout.fillWidth:true; text:"简洁双行"; selected:!live.singleLine && !live.traditional; implicitHeight:32; onClicked:controller.setLiveLayout("double") }
                                StudioButton { objectName:"liveQuickDynamic"; Layout.fillWidth:true; text:"单行大字"; selected:live.singleLine; implicitHeight:32; onClicked:controller.setLiveLayout("single") }
                                StudioButton { objectName:"liveStyleSettings"; Layout.fillWidth:true; text:"字幕特效"; implicitHeight:32; onClicked:live.styleRequested() }
                            }
                            Rectangle { Layout.fillWidth:true; Layout.preferredHeight:1; color:Theme.hairline; Layout.topMargin:4 }
                            DarkCheckBox {
                                objectName:"liveLyricSweep"; Layout.leftMargin:10; text:"歌词走带"
                                checked:live.settings.sweepMode!=="sentence"
                                ToolTip.visible:hovered; ToolTip.text:"所有字幕样式开启或关闭跟唱扫色 · Ctrl+Alt+K"
                                onToggled:controller.setLyricSweep(checked)
                                Accessible.onToggleAction:controller.setLyricSweep(live.settings.sweepMode==="sentence")
                            }
                            DarkCheckBox {
                                objectName:"liveDuetColors"; Layout.leftMargin:10; text:"对唱分色"
                                checked:!!stateData.duetColors
                                ToolTip.visible:hovered; ToolTip.text:"按歌词里的「男：」「女：」「合：」标记，男蓝、女粉、合唱绿"
                                onToggled:controller.setDuetColors(checked)
                            }
                            Item { Layout.fillHeight:true }
                        }
                        ScreenContentPanel { id:contentPanel; stateData:live.stateData }
                    }
                }
            }
        }
    }
    RekordboxAxBinding {
        id:axBinding; axState:live.axState
        width:Math.min(680,live.width-28); x:(live.width-width)/2; y:Math.max(12,(live.height-height)/2)
    }
    Popup {
        id:screenPopup; objectName:"liveScreenPopup"; width:355; height:238; padding:16
        x:Math.max(14,live.width-width-14); y:54
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background:Rectangle { color:Theme.elevated; radius:Theme.radiusCard; border.color:Theme.strokeStrong }
        ColumnLayout {
            anchors.fill:parent; spacing:10
            Text { text:"客屏显示器"; color:Theme.text; font.pixelSize:16; font.weight:Font.Medium }
            DarkComboBox { objectName:"liveOutputScreenCombo"; Layout.fillWidth:true; model:live.screenLabels; currentIndex:live.screenIndex; onActivated:controller.setOutputTarget(index===0 ? "" : live.screens[index-1].name, live.target.fullScreen) }
            DarkCheckBox { objectName:"liveFullscreenToggle"; text:"全屏显示客屏"; checked:live.target.fullScreen; palette.windowText:Theme.text; onClicked:controller.setOutputTarget(live.target.screenName,checked) }
            Text { Layout.fillWidth:true; text:live.missingScreen ? "保存的显示器已断开，客屏退回普通窗口。请重新选择。" : "设置立即作用于独立输出窗口。断开显示器时返回普通窗口。"; color:live.missingScreen ? Theme.amber : Theme.secondary; font.pixelSize:11; wrapMode:Text.Wrap }
            RowLayout {
                Layout.fillWidth:true
                StudioButton { objectName:"liveRefreshScreens"; text:"刷新显示器"; onClicked:controller.refreshOutputScreens() }
                Item { Layout.fillWidth:true }
                StudioButton { text:"完成"; tone:"primary"; onClicked:screenPopup.close() }
            }
        }
    }
    Popup {
        id:keyHelp; objectName:"liveKeyHelp"; width:Math.min(420, live.width-28); padding:18
        x:(live.width-width)/2; y:Math.max(12,(live.height-height)/2); modal:true
        closePolicy:Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background:Rectangle { color:Theme.elevated; radius:Theme.radiusCard; border.color:Theme.strokeStrong }
        readonly property string mod: Qt.platform.os === "osx" ? "⌃" : "Ctrl+"
        contentItem: ColumnLayout {
            spacing:8
            Text { text:"现场快捷键"; color:Theme.text; font.pixelSize:16; font.weight:Font.Medium }
            Repeater {
                model:[
                    {k:"T", d:"打拍对齐：一句刚开唱时按"},
                    {k:"Shift+T", d:"撤销上一次打拍"},
                    {k:"[  /  ]", d:"歌词提前 / 延后 0.1 秒"},
                    {k:"\\", d:"同步微调归零"},
                    {k:keyHelp.mod+"H", d:"隐藏 / 恢复歌词"},
                    {k:"Ctrl+Alt+1 / 2", d:"指定 A / B 盘"},
                    {k:"Ctrl+Alt+0", d:"自动跟随"},
                    {k:(Qt.platform.os === "osx" ? "⌃⌥K" : "Ctrl+Alt+K"), d:"歌词走带开关"},
                    {k:"Ctrl+Shift+O", d:"打开 / 关闭客屏"},
                    {k:"?", d:"打开这张表"},
                    {k:"MIDI", d:"设置与帮助 → MIDI 控制器"}]
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth:true; spacing:12
                    Rectangle {
                        Layout.preferredWidth:116; Layout.preferredHeight:24; radius:5; color:Theme.sunken
                        Text { anchors.centerIn:parent; text:modelData.k; color:Theme.accent; font.family: Theme.numberFont; font.pixelSize:12 }
                    }
                    Text { Layout.fillWidth:true; text:modelData.d; color:Theme.text; font.pixelSize:13; elide:Text.ElideRight }
                }
            }
            RowLayout {
                Layout.fillWidth:true; Layout.topMargin:4
                Text { Layout.fillWidth:true; text:"单键只在现场页有效。"; color:Theme.muted; font.pixelSize:11 }
                StudioButton { objectName:"liveKeyHelpClose"; text:"知道了"; tone:"primary"; onClicked:keyHelp.close() }
            }
        }
    }
}
