import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    property var stateData: ({})
    signal apiRequested()
    readonly property var preparation: stateData.preparation || ({})
    readonly property var configuration: preparation.config || ({})
    readonly property var routes: preparation.routes || [{id: "domestic", label: "国内路线", service: "302 豆包演唱字幕"}, {id: "international", label: "国际路线", service: "302 豆包演唱字幕"}]
    function phaseTone(phase) { return ["ready", "complete", "corrected"].indexOf(phase) >= 0 ? "green" : ["failed", "error"].indexOf(phase) >= 0 ? "red" : "amber"; }
    function routeIndex() { for (var i = 0; i < routes.length; ++i) if (routes[i].id === preparation.route) return i; return 0; }

    ScrollView {
        id: scroll
        objectName: "recognitionScroll"
        anchors.fill: parent
        anchors.leftMargin: Theme.pagePadding; anchors.rightMargin: Theme.pagePadding; anchors.bottomMargin: 12; anchors.topMargin: 4
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: scroll.availableWidth
            spacing: 16
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Text { text: "歌曲准备"; color: Theme.text; font.pixelSize: Theme.fontSection; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; Accessible.role: Accessible.Heading; Accessible.name: text }
                    Text { Layout.fillWidth: true; text: "按实际音频版本准备歌词；切歌后旧任务留在原版本，新曲不会继承旧歌词。"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
                    Text { Layout.fillWidth: true; text: "已有字幕：在对应盘点击「导入本地字幕」，支持 SRT / LRC / JSON；保留原时码，直接在本机使用。"; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
                }
                StatusPill { text: configuration.newSubmissionEnabled ? "新提交已允许" : "新提交关闭"; tone: configuration.newSubmissionEnabled ? "amber" : "blue" }
                ActionButton { objectName: "recognitionAPIButton"; text: "配置模型 API"; tone: "primary"; implicitHeight: 34; onClicked: panel.apiRequested() }
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: configContents.implicitHeight + 32
                radius: Theme.radiusCard
                color: Theme.surface
                border.color: Theme.hairline
                ColumnLayout {
                    id: configContents
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 16
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: configuration.provider === "bailian" ? "百炼单首与批量识别" : "302 旧任务与批量准备"; color: Theme.text; font.pixelSize: Theme.fontSection; font.weight: Font.Medium; Layout.fillWidth: true }
                        StudioButton { objectName: "recognitionConfigButton"; visible:configuration.provider !== "bailian"; text: "导入旧配置"; implicitHeight: 32; font.pixelSize: 11; onClicked: controller.choosePreparationConfig() }
                    }
                    Text { objectName: "recognitionConfigStatus"; Layout.fillWidth: true; text: configuration.summary || "尚未选择服务配置；先配置服务凭据和本机额度。此面板不显示密钥。"; color: configuration.ready ? Theme.green : Theme.amber; font.pixelSize: 12; wrapMode: Text.Wrap }
                    Text { visible: !!configuration.path; Layout.fillWidth: true; text: configuration.path || ""; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.WrapAnywhere }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Text { text: "路线"; color: Theme.secondary; font.pixelSize: 12 }
                        DarkComboBox {
                            objectName: "recognitionRouteCombo"
                            Layout.preferredWidth: 176
                            model: panel.routes
                            textRole: "label"
                            currentIndex: panel.routeIndex(); enabled:configuration.provider !== "bailian"
                            onActivated: controller.setRecognitionRoute(panel.routes[index].id)
                        }
                        Text { text: "本机额度"; color: Theme.secondary; font.pixelSize: 12 }
                        TextField {
                            id: budgetField
                            objectName: "recognitionBudgetField"
                            Layout.preferredWidth: 102
                            implicitHeight: 36
                            placeholderText: "用户配置"
                            color: Theme.text
                            placeholderTextColor: Theme.muted
                            selectByMouse: true
                            validator: DoubleValidator { bottom: 0; top: 1000; decimals: 4; notation: DoubleValidator.StandardNotation }
                            onEditingFinished: if (acceptableInput && text.length && Number(text) !== panel.configuration.budgetLimit) controller.setRecognitionBudget(Number(text))
                            Binding { target: budgetField; property: "text"; value: panel.configuration.budgetLimit === undefined ? "" : String(panel.configuration.budgetLimit); when: !budgetField.activeFocus; restoreMode: Binding.RestoreNone }
                            leftPadding: 10
                            background: Rectangle { radius: Theme.radiusControl; color: Theme.sunken; border.color: budgetField.activeFocus ? Theme.focus : Theme.stroke; border.width: budgetField.activeFocus ? 2 : 1 }
                        }
                        Text { Layout.fillWidth: true; text: configuration.budgetUnit || "PTC"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
                    }
                    DarkCheckBox {
                        objectName: "recognitionAllowSubmission"
                        text: "允许本次手动新提交（本机额度内）"
                        checked: !!configuration.newSubmissionEnabled
                        palette.windowText: Theme.secondary
                        onClicked: controller.setNewSubmissionEnabled(checked)
                    }
                    Text { Layout.fillWidth: true; text: "本机已预留 " + Number(configuration.spent || 0).toFixed(4) + " " + (configuration.budgetUnit || "PTC") + " · 剩余 " + Math.max(0, Number(configuration.budgetLimit || 0) - Number(configuration.spent || 0)).toFixed(4) + " " + (configuration.budgetUnit || "PTC"); color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
                    Text { Layout.fillWidth: true; text: "新提交默认关闭。先审阅具体曲目、上传源、服务和预算，再按盘启动；已有任务通过续查恢复。额度适用于本页与曲库批量，不代表账户余额；制作页单首和文字检查另外确认费用。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.35 }
                }
            }
            Text { Layout.fillWidth: true; text: panel.stateData.notice || ""; color: Theme.amber; font.pixelSize: 11; wrapMode: Text.Wrap }
            GridLayout {
                objectName: "recognitionDeckGrid"
                Layout.fillWidth: true; columns: scroll.availableWidth >= 1180 ? 2 : 1; columnSpacing: 14; rowSpacing: 14
            Repeater {
                model: ["A", "B"]
                delegate: Rectangle {
                    id: deckPanel
                    required property string modelData
                    readonly property var currentDeck: panel.stateData["deck" + modelData] || ({})
                    readonly property var preparationDeck: (panel.preparation.decks || ({}))[modelData] || ({})
                    readonly property var plan: preparationDeck.plan || ({})
                    readonly property bool reviewable: !!(plan.planId && plan.title && plan.versionId && plan.sourcePath && plan.uploadSource && plan.service && plan.routeLabel && plan.durationText && plan.budgetLabel) && plan.estimatedCost !== undefined
                    Layout.fillWidth: true; Layout.alignment: Qt.AlignTop
                    implicitHeight: deckContents.implicitHeight + 32
                    radius: Theme.radiusCard
                    color: Theme.surface
                    border.color: Theme.hairline
                    ColumnLayout {
                        id: deckContents
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 16
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Rectangle { Layout.preferredWidth: 28; Layout.preferredHeight: 28; radius: 7; color: Theme.elevated; Text { anchors.centerIn: parent; text: deckPanel.modelData; color: Theme.accent; font.pixelSize: 15; font.weight: Font.Bold } Accessible.name: deckPanel.modelData + " 盘" }
                            Text { Layout.fillWidth: true; text: deckPanel.currentDeck.title || "等待歌曲"; color: Theme.text; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight }
                            StatusPill { text: deckPanel.preparationDeck.label || "等待已核验音频"; tone: panel.phaseTone(deckPanel.preparationDeck.phase || "") }
                        }
                        Text { objectName: "recognitionVersion-" + deckPanel.modelData; Layout.fillWidth: true; text: deckPanel.preparationDeck.versionId || deckPanel.currentDeck.versionId || "尚无音频版本"; color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10; wrapMode: Text.WrapAnywhere }
                        Text { objectName: "recognitionDetail-" + deckPanel.modelData; Layout.fillWidth: true; text: deckPanel.preparationDeck.detail || "识别前先核验实际音频文件；合成回放不提交云端。"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
                        Rectangle {
                            visible: deckPanel.reviewable
                            Layout.fillWidth: true
                            implicitHeight: planContents.implicitHeight + 24
                            radius: Theme.radiusControl
                            color: Theme.amberSoft
                            border.color: Theme.amberLine
                            ColumnLayout {
                                id: planContents
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 12
                                spacing: 7
                                Text { text: "可审阅提交计划 · " + (deckPanel.plan.title || ""); color: Theme.amber; font.pixelSize: 13; font.weight: Font.Medium }
                                Text { Layout.fillWidth: true; text: "实际版本  " + (deckPanel.plan.versionId || ""); color: Theme.secondary; font.pixelSize: 10; wrapMode: Text.WrapAnywhere }
                                Text { Layout.fillWidth: true; text: "上传源  " + (deckPanel.plan.sourcePath || "") + "\n上传方式  " + (deckPanel.plan.uploadSource || ""); color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; lineHeight: 1.4 }
                                Text { Layout.fillWidth: true; text: "服务  " + (deckPanel.plan.service || "") + " · " + (deckPanel.plan.routeLabel || "") + "\n音频时长  " + (deckPanel.plan.durationText || "未解析") + "\n预估费用  " + (deckPanel.plan.estimatedCost !== undefined ? String(deckPanel.plan.estimatedCost) : "未解析") + "\n本机预算  " + (deckPanel.plan.budgetLabel || "未配置"); color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.4 }
                                Text { visible: !!deckPanel.plan.blockedReason; Layout.fillWidth: true; text: deckPanel.plan.blockedReason || ""; color: Theme.red; font.pixelSize: 11; wrapMode: Text.Wrap }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            Layout.preferredHeight: childrenRect.height
                            spacing: 8
                            ActionButton { objectName: "recognitionVerify-" + deckPanel.modelData; text: "核验当前音频"; visible: panel.stateData.inputMode === "serato" && !!deckPanel.currentDeck.path && !deckPanel.currentDeck.identityReady; implicitHeight: 32; font.pixelSize: 12; onClicked: controller.verifySeratoPreparation(deckPanel.modelData) }
                            ActionButton { objectName: "recognitionLocal-" + deckPanel.modelData; text: "导入本地字幕"; tone: "primary"; implicitHeight: 32; font.pixelSize: 12; enabled: !!deckPanel.currentDeck.identityReady; onClicked: controller.chooseLocalSubtitles(deckPanel.modelData) }
                            ActionButton { objectName: "recognitionPlan-" + deckPanel.modelData; text: "审阅识别计划"; implicitHeight: 32; font.pixelSize: 12; enabled: panel.stateData.inputMode !== "serato" || !!deckPanel.currentDeck.identityReady; onClicked: controller.planRecognition(deckPanel.modelData) }
                            ActionButton { objectName: "recognitionStart-" + deckPanel.modelData; visible: deckPanel.reviewable; text: "启动识别"; tone: "primary"; implicitHeight: 32; font.pixelSize: 12; enabled: deckPanel.reviewable && !!deckPanel.plan.canSubmit && !!panel.configuration.ready && !!panel.configuration.newSubmissionEnabled; onClicked: controller.startRecognition(deckPanel.modelData, deckPanel.plan.planId) }
                            ActionButton { objectName: "recognitionQuery-" + deckPanel.modelData; text: "继续查询"; implicitHeight: 32; font.pixelSize: 12; enabled: !!deckPanel.preparationDeck.canQuery; onClicked: controller.queryRecognition(deckPanel.modelData) }
                            ActionButton { objectName: "recognitionCorrect-" + deckPanel.modelData; text: "选择参考歌词"; implicitHeight: 32; font.pixelSize: 12; enabled: !!deckPanel.preparationDeck.canCorrect; onClicked: controller.chooseReferenceLyrics(deckPanel.modelData) }
                            ActionButton { objectName: "recognitionLrc-" + deckPanel.modelData; text: "导出 LRC"; implicitHeight: 32; font.pixelSize: 12; enabled: !!deckPanel.preparationDeck.canExport; onClicked: controller.exportLyrics(deckPanel.modelData, "lrc") }
                            ActionButton { objectName: "recognitionSrt-" + deckPanel.modelData; text: "导出 SRT"; implicitHeight: 32; font.pixelSize: 12; enabled: !!deckPanel.preparationDeck.canExport; onClicked: controller.exportLyrics(deckPanel.modelData, "srt") }
                        }
                        Text { visible: !!deckPanel.preparationDeck.referencePath; Layout.fillWidth: true; text: "参考歌词  " + (deckPanel.preparationDeck.referencePath || ""); color: Theme.muted; font.pixelSize: 10; wrapMode: Text.WrapAnywhere }
                        Text { visible: !!deckPanel.preparationDeck.correctionSummary; Layout.fillWidth: true; text: deckPanel.preparationDeck.correctionSummary || ""; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
                    }
                }
            }
            }
            Text { Layout.fillWidth: true; text: "输出歌词只使用当前版本已校验时间轴。参考文本用于纠错；缺少字级时码时使用句级显示。自动搜索参考歌词未接入。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.bottomMargin: 12 }
        }
    }
}
