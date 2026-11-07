import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ScreenLayerStyles.js" as Styles

ColumnLayout {
    id: panel
    objectName: "screenContentPanel"
    property var stateData: ({})
    property int tab: 0
    property var draftText: ({})
    property var draftLogo: ({})
    property string draftLogoUrl: ""
    property bool syncing: false
    property bool previewActive: false
    property bool adjusting: false
    property bool choosingStyle: false
    property int seconds: 15
    property string activeTemplate: ""
    property bool fillingTemplate: false
    property var pendingProfile: ({})
    property var cachedLibrary: ({phrases: [], history: []})
    property string librarySignature: ""
    readonly property var personalLibrary: cachedLibrary
    readonly property bool previewAvailable: tab < 2 || (tab === 3 && !!pendingProfile.layers)
    readonly property var draftLayers: ({text: draftText, logo: draftLogo})
    readonly property var config: stateData.screenLayers || ({text: {}, logo: {}})
    readonly property var activeDraft: tab === 0 ? draftText : draftLogo
    readonly property string layerName: tab === 0 ? "text" : "logo"
    readonly property var styles: tab === 0 ? Styles.text : Styles.logo
    readonly property bool previewExample: choosingStyle && (tab === 0 ? !draftText.text : tab === 1 && !draftLogo.image)
    readonly property int draftLength: textInput.text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "x").length
    readonly property bool canPublishText: textInput.text.trim().length > 0 && draftLength <= 300 && textInput.text.indexOf("___") < 0 && textInput.text.indexOf("{姓名}") < 0
    readonly property bool textOn: !!config.text.visible || !!(stateData.guestMessage || {}).visible
    readonly property var positionKeys: ["top-left", "top-center", "top-right", "center-left", "center", "center-right", "bottom-left", "bottom-center", "bottom-right"]
    readonly property var previewState: makePreview()
    spacing: 7
    onStateDataChanged: cacheLibrary()
    onTabChanged: { previewActive = false; adjusting = false; choosingStyle = false; if (contentScroll.contentItem) contentScroll.contentItem.contentY = 0; }

    function clone(value) { return JSON.parse(JSON.stringify(value)); }
    function cacheLibrary() {
        var value = stateData.screenContentLibrary || {phrases: [], history: []};
        var signature = JSON.stringify(value);
        if (signature !== librarySignature) { cachedLibrary = clone(value); librarySignature = signature; }
    }
    function fillName() {
        fillingTemplate = true;
        textInput.text = activeTemplate.split("{姓名}").join(personName.text.trim() || "___");
        fillingTemplate = false;
    }
    function usePhrase(row) {
        tab = 0; activeTemplate = ""; personName.text = ""; activeTemplate = row.text;
        fillName();
        if (activeTemplate.indexOf("{姓名}") >= 0) personName.forceActiveFocus();
        else textInput.forceActiveFocus();
    }
    function useHistory(entry) {
        tab = 0; activeTemplate = ""; syncing = true;
        draftText = clone(entry.row); textInput.text = draftText.text; syncing = false;
        seconds = [15,30,60,0].indexOf(entry.seconds) >= 0 ? entry.seconds : 15;
        previewActive = true; textInput.forceActiveFocus();
    }
    function sync(layer) {
        syncing = true;
        if (!layer || layer === "text") {
            draftText = clone(config.text);
            if (!draftText.text && draftText.style === "classic" && draftText.position === "center" && draftText.fontSize === 48) {
                draftText.position = "top-center"; draftText.fontSize = 40;
            }
            textInput.text = draftText.text || "";
        }
        if (!layer || layer === "logo") { draftLogo = clone(config.logo); draftLogoUrl = stateData.logoImageUrl || ""; }
        syncing = false;
        previewActive = false;
    }
    function update(key, value) {
        var next = clone(activeDraft); next[key] = value;
        if (tab === 0) draftText = next; else draftLogo = next;
        if (!syncing) previewActive = true;
    }
    function chooseStyle(preset) {
        if (tab === 0) {
            draftText = Object.assign({}, draftText, {style: preset.key, position: preset.position,
                                                      fontSize: preset.fontSize, color: preset.color});
        } else {
            draftLogo = Object.assign({}, draftLogo, {style: preset.key, position: preset.position, size: preset.size});
        }
        previewActive = true;
    }
    function makePreview() {
        if (!previewActive || !previewAvailable) return stateData;
        var next = Object.assign({}, stateData);
        if (tab === 3) {
            next.screenLayers = clone(pendingProfile.layers);
            next.screenLayers.logo.visible = !!next.screenLayers.logo.image;
            next.logoImageUrl = pendingProfile.logoImageUrl;
            next.guestMessage = {visible: false, text: ""};
            return next;
        }
        next.screenLayers = clone(config);
        next.screenLayers[layerName] = clone(activeDraft);
        next.screenLayers[layerName].visible = previewExample || (tab === 0 ? !!draftText.text : !!draftLogo.image);
        if (tab === 0) {
            if (previewExample) next.screenLayers.text.text = "今晚，尽兴唱";
            if (!/^#[0-9a-fA-F]{6}$/.test(next.screenLayers.text.color || "")) next.screenLayers.text.color = config.text.color;
            next.guestMessage = {visible: false, text: ""};
            next.screenTextValidUntilNs = 0;
        } else next.logoImageUrl = previewExample ? Qt.resolvedUrl("ScreenLayerSample.svg").toString() : draftLogoUrl;
        return next;
    }
    function publish() {
        if (tab === 0 && canPublishText && controller.applyScreenContent("text", draftText, seconds)) sync("text");
        else if (tab === 1 && draftLogo.image && controller.applyScreenContent("logo", draftLogo, 0)) sync("logo");
    }
    function chooseLogo(url) {
        var asset = url ? controller.prepareScreenLogo(url) : controller.prepareScreenLogo();
        if (!asset.image) return;
        draftLogo = Object.assign({}, draftLogo, {image: asset.image, fileName: asset.fileName, visible: true});
        draftLogoUrl = asset.url; previewActive = true;
    }
    function hideText() {
        if (controller.hideScreenContent("text")) previewActive = false;
    }
    Component.onCompleted: { cacheLibrary(); sync(); }

    Text {
        objectName: "screenContentStatus"
        Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10
        text: (panel.stateData.outputOpen ? "" : "仅本机预览 · ") + (panel.textOn ? "文字启用" + ((panel.stateData.screenTextRemaining || panel.stateData.guestMessageRemaining) ? " · 剩余 " + (panel.stateData.screenTextRemaining || panel.stateData.guestMessageRemaining) + " 秒" : " · 固定显示") : "文字已撤下")
              + "    标识" + (panel.config.logo.visible ? "显示中" : "已隐藏")
        color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap
    }
    RowLayout {
        Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; spacing: 0
        TextTab { objectName: "screenLayersTextTab"; Layout.fillWidth: true; text: "喊话"; selected: panel.tab === 0; onClicked: { panel.tab = 0; panel.previewActive = false; } }
        TextTab { objectName: "screenLayersLogoTab"; Layout.fillWidth: true; text: "DJ 标识"; selected: panel.tab === 1; onClicked: { panel.tab = 1; panel.previewActive = false; } }
        TextTab { objectName: "screenWishesTab"; Layout.fillWidth: true; text: "祝福" + ((panel.stateData.guestInbox || []).length ? " " + panel.stateData.guestInbox.length : ""); selected: panel.tab === 2; onClicked: { panel.tab = 2; panel.previewActive = false; } }
        TextTab { objectName: "screenPersonalTab"; Layout.fillWidth: true; text: "我的"; selected: panel.tab === 3; onClicked: panel.tab = 3 }
    }
    ScrollView {
        id: contentScroll
        objectName: "screenContentScroll"
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 10; Layout.rightMargin: 10
        clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 8
            ColumnLayout {
                visible: panel.tab === 0; Layout.fillWidth: true; spacing: 7
                GridLayout {
                    Layout.fillWidth: true; columns: 2; columnSpacing: 5; rowSpacing: 4
                    Repeater {
                        model: (panel.personalLibrary.phrases || []).slice(0,4)
                        delegate: StudioButton {
                            required property var modelData
                            objectName: "screenPhrase-" + modelData.label
                            Layout.fillWidth: true; text: modelData.label; implicitHeight: 26; font.pixelSize: 11
                            onClicked: panel.usePhrase(modelData)
                        }
                    }
                }
                DarkComboBox {
                    objectName: "screenMorePhrases"; visible: (panel.personalLibrary.phrases || []).length > 4; Layout.fillWidth: true; implicitHeight: 27
                    model: ["更多常用语…"].concat((panel.personalLibrary.phrases || []).map(function(row) { return row.label; }))
                    onActivated: { if (currentIndex > 0) panel.usePhrase(panel.personalLibrary.phrases[currentIndex-1]); currentIndex = 0; }
                }
                TextField {
                    id: personName; objectName: "screenPhrasePerson"; visible: panel.activeTemplate.indexOf("{姓名}") >= 0
                    Layout.fillWidth: true; implicitHeight: 28; maximumLength: 30; placeholderText: "填写姓名后预览"; color: Theme.text; placeholderTextColor: Theme.muted; selectByMouse: true
                    onTextChanged: { if (panel.activeTemplate.indexOf("{姓名}") >= 0) panel.fillName(); }
                    background: Rectangle { color: Theme.sunken; radius: 5; border.color: activeFocus ? Theme.focus : Theme.strokeStrong }
                }
                ScrollView {
                    Layout.fillWidth: true; Layout.preferredHeight: 74
                    TextArea {
                        id: textInput; objectName: "screenTextInput"
                        placeholderText: "输入喊话内容，先预览再上屏…"; textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap; selectByMouse: true; color: Theme.text; placeholderTextColor: Theme.muted
                        font.pixelSize: 13
                        onTextChanged: { if (!panel.syncing) { if (!panel.fillingTemplate) panel.activeTemplate = ""; panel.update("text", text); } }
                        background: Rectangle { color: Theme.sunken; radius: 6; border.color: textInput.activeFocus ? Theme.focus : Theme.strokeStrong }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 4
                    Text { objectName: "screenTextCount"; Layout.fillWidth: true; text: textInput.text.indexOf("___") >= 0 ? "先填写姓名，再上屏" : panel.draftLength + " / 300 字"; color: panel.canPublishText || !textInput.text ? Theme.secondary : Theme.amber; font.pixelSize: 11; elide: Text.ElideRight }
                    StudioButton { objectName: "screenSaveCurrentPhrase"; text: "存为常用语…"; enabled: !!textInput.text.trim(); implicitHeight: 24; font.pixelSize: 11; padding: 5; onClicked: { personal.editCurrent(panel.activeTemplate || textInput.text); panel.tab = 3; } }
                }
            }
            ColumnLayout {
                visible: panel.tab === 1; Layout.fillWidth: true; spacing: 8
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 90
                    color: logoDrop.containsDrag ? Theme.accentSoft : Theme.sunken
                    border.color: logoDrop.containsDrag ? Theme.accentLine : Theme.hairline; radius: 6
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 8; spacing: 8
                        Image { Layout.preferredWidth: 60; Layout.preferredHeight: 52; source: panel.draftLogoUrl; fillMode: Image.PreserveAspectFit; visible: !!panel.draftLogoUrl; asynchronous: true }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Text { Layout.fillWidth: true; text: panel.draftLogo.fileName || "拖入 Logo 图片"; textFormat: Text.PlainText; color: Theme.secondary; font.pixelSize: 12; elide: Text.ElideMiddle }
                            StudioButton { objectName: "screenLogoChoose"; Layout.fillWidth: true; text: panel.draftLogo.image ? "更换图片…" : "选择图片…"; iconName: "import"; implicitHeight: 28; padding: 5; font.pixelSize: 11; onClicked: panel.chooseLogo() }
                        }
                    }
                    DropArea {
                        id: logoDrop
                        objectName: "screenLogoDrop"
                        anchors.fill: parent
                        onEntered: function(drag) { drag.accepted = drag.hasUrls && drag.urls.length === 1 && drag.urls[0].toString().indexOf("file:") === 0; }
                        onDropped: function(drop) { panel.chooseLogo(drop.urls[0].toString()); drop.acceptProposedAction(); }
                    }
                }
                Text { Layout.fillWidth: true; text: "PNG / JPG / WebP · 推荐透明 PNG"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
            }
            ColumnLayout {
                visible: panel.tab < 2; Layout.fillWidth: true; spacing: 6
                RowLayout {
                    Layout.fillWidth: true; spacing: 5
                    StudioButton {
                        objectName: "screenStyleToggle"; Layout.fillWidth: true; Layout.minimumWidth: 0; implicitHeight: 28
                        text: panel.choosingStyle ? "收起模板" : "模板  /  " + Styles.find(panel.styles, panel.activeDraft.style).label
                        textAlignment: Text.AlignLeft; font.pixelSize: 12; padding: 6
                        selected: panel.choosingStyle
                        onClicked: panel.choosingStyle = !panel.choosingStyle
                    }
                    DarkComboBox {
                        objectName: "screenTextDuration"; visible: panel.tab === 0; Layout.preferredWidth: 93; Layout.minimumWidth: 0; implicitHeight: 28
                        model: ["15 秒", "30 秒", "60 秒", "固定显示"]
                        Accessible.name: "文字显示时长"
                        currentIndex: [15,30,60,0].indexOf(panel.seconds)
                        onActivated: panel.seconds = [15,30,60,0][currentIndex]
                    }
                }
                Loader {
                    Layout.fillWidth: true; active: panel.choosingStyle && panel.tab < 2
                    visible: active
                    sourceComponent: ScreenLayerStylePicker {
                        styles: panel.styles; logo: panel.tab === 1; selectedStyle: Styles.find(panel.styles, panel.activeDraft.style).key
                        onChosen: function(style) { panel.chooseStyle(style); }
                    }
                }
                StudioButton { objectName: "screenAdjustToggle"; text: panel.adjusting ? "收起位置 / 外观" : "调整位置 / 外观"; implicitHeight: 26; font.pixelSize: 11; onClicked: panel.adjusting = !panel.adjusting }
                ColumnLayout {
                    visible: panel.adjusting; Layout.fillWidth: true; spacing: 6
                    DarkComboBox { objectName: "screenLayerPosition"; Layout.fillWidth: true; model: ["左上", "上方居中", "右上", "左侧居中", "正中", "右侧居中", "左下", "下方居中", "右下"]; currentIndex: Math.max(0, panel.positionKeys.indexOf(panel.activeDraft.position)); onActivated: panel.update("position", panel.positionKeys[currentIndex]) }
                    Text { text: (panel.tab === 0 ? "字号 " : "大小 ") + Math.round(panel.tab === 0 ? panel.draftText.fontSize || 40 : panel.draftLogo.size || 18) + (panel.tab === 1 ? "%" : ""); color: Theme.secondary; font.pixelSize: 11 }
                    ControlSlider { objectName: "screenLayerSize"; Layout.fillWidth: true; from: panel.tab === 0 ? 24 : 5; to: panel.tab === 0 ? 120 : 40; stepSize: 1; value: (panel.tab === 0 ? panel.draftText.fontSize : panel.draftLogo.size) || from; onMoved: panel.update(panel.tab === 0 ? "fontSize" : "size", value) }
                    Text { text: "不透明度 " + Math.round((panel.activeDraft.opacity === undefined ? 1 : panel.activeDraft.opacity) * 100) + "%"; color: Theme.secondary; font.pixelSize: 11 }
                    ControlSlider { objectName: "screenLayerOpacity"; Layout.fillWidth: true; from: 10; to: 100; stepSize: 1; value: panel.activeDraft.opacity === undefined ? 100 : panel.activeDraft.opacity * 100; onMoved: panel.update("opacity", value / 100) }
                    RowLayout {
                        visible: panel.tab === 0; Layout.fillWidth: true
                        Text { text: "字色"; color: Theme.secondary; font.pixelSize: 11 }
                        TextField { objectName: "screenTextColor"; Layout.fillWidth: true; text: panel.draftText.color || "#FFFFFF"; maximumLength: 7; selectByMouse: true; color: Theme.text; implicitHeight: 28; onTextEdited: panel.update("color", text); background: Rectangle { color: Theme.sunken; radius: 5; border.color: Theme.strokeStrong } }
                    }
                    StudioButton { objectName: "screenLogoRemove"; visible: panel.tab === 1; text: "移除已存标识"; enabled: !!panel.config.logo.image; onClicked: { if (controller.removeScreenLogo()) panel.sync("logo"); } }
                }
                Text { Layout.fillWidth: true; visible: panel.previewActive; text: "修改仅在本机预览，点击下方按钮才上屏"; color: Theme.amber; font.pixelSize: 10; wrapMode: Text.Wrap }
                Text { Layout.fillWidth: true; visible: panel.tab === 1 && panel.draftLogo.position === "top-right" && !!(panel.stateData.guestQr || {}).onScreen; text: "右上角有扫码二维码，建议选左上角标"; color: Theme.amber; font.pixelSize: 10; wrapMode: Text.Wrap }
            }
            GuestWishControls { visible: panel.tab === 2; Layout.fillWidth: true; stateData: panel.stateData }
            ScreenPersonalPanel {
                id: personal; objectName: "screenPersonalPanel"; visible: panel.tab === 3; Layout.fillWidth: true
                libraryData: panel.personalLibrary; draftLayers: panel.draftLayers
                onPhraseSelected: function(phrase) { panel.usePhrase(phrase); }
                onHistorySelected: function(entry) { panel.useHistory(entry); }
                onProfilePrepared: function(profile) { panel.pendingProfile = profile; panel.previewActive = true; }
                onProfileApplied: { panel.pendingProfile = ({}); panel.activeTemplate = ""; personName.text = ""; panel.sync(); }
                onProfileCancelled: { panel.pendingProfile = ({}); panel.previewActive = false; }
                onRequestTop: { if (contentScroll.contentItem) contentScroll.contentItem.contentY = 0; }
            }
        }
    }
    Text { objectName: "screenLayersNotice"; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; visible: !!stateData.screenLayerNotice; text: stateData.screenLayerNotice || ""; color: Theme.amber; font.pixelSize: 11; wrapMode: Text.Wrap }
    Text { Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; visible: panel.tab === 3 && !!panel.pendingProfile.layers; text: "采用后替换常用语和布局、切换标识，\n并撤下当前喊话。"; color: Theme.amber; font.pixelSize: 10; wrapMode: Text.Wrap }
    RowLayout {
        visible: panel.tab < 2 || (panel.tab === 3 && !!panel.pendingProfile.layers); Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; spacing: 5
        ActionButton { objectName: "screenProfileApply"; visible: panel.tab === 3; Layout.fillWidth: true; text: "采用此配置"; tone: "primary"; implicitHeight: 32; font.pixelSize: 12; onClicked: personal.applyPending() }
        StudioButton { objectName: "screenProfileCancel"; visible: panel.tab === 3; text: "取消"; implicitHeight: 32; font.pixelSize: 12; onClicked: personal.cancelPending() }
        ActionButton { objectName: "screenTextApply"; visible: panel.tab === 0; Layout.fillWidth: true; text: (panel.config.text.visible ? "更新" : "上屏") + (panel.seconds ? " " + panel.seconds + " 秒" : " · 固定"); tone: "primary"; implicitHeight: 32; font.pixelSize: 12; hint: Qt.platform.os === "osx" ? "输入时可按 ⌃↩ 上屏，普通回车换行" : "输入时可按 Ctrl+Enter 上屏，普通回车换行"; enabled: panel.canPublishText; onClicked: panel.publish() }
        StudioButton { objectName: "screenTextToggle"; visible: panel.tab === 0; text: "撤下"; enabled: panel.textOn; implicitHeight: 32; font.pixelSize: 12; onClicked: panel.hideText() }
        ActionButton { objectName: "screenLogoApply"; visible: panel.tab === 1; Layout.fillWidth: true; text: panel.config.logo.visible ? "更新标识" : "显示标识"; tone: "primary"; implicitHeight: 32; font.pixelSize: 12; enabled: !!panel.draftLogo.image; onClicked: panel.publish() }
        StudioButton { objectName: "screenLogoToggle"; visible: panel.tab === 1; text: "隐藏"; enabled: !!panel.config.logo.visible; implicitHeight: 32; font.pixelSize: 12; onClicked: { if (controller.hideScreenContent("logo")) panel.previewActive = false; } }
    }
    StudioButton { objectName: "screenExtrasHide"; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; Layout.bottomMargin: 8; text: "撤下全部附加内容"; implicitHeight: 28; font.pixelSize: 11; hint: "撤下文字、标识、祝福和二维码；保留素材与待审内容"; onClicked: { if (controller.hideScreenExtras()) panel.previewActive = false; } }
    Shortcut { sequence: Qt.platform.os === "osx" ? "Meta+Return" : "Ctrl+Return"; enabled: panel.visible && panel.tab === 0 && textInput.activeFocus && panel.canPublishText; onActivated: panel.publish() }
}
