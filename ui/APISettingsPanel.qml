import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    objectName:"apiSettingsPanel"
    property var apiModel: typeof aiSettings!=="undefined" ? aiSettings : null
    readonly property var api: apiModel ? apiModel.state : ({})
    property string kind:"speech"
    readonly property var config: api[kind] || ({})
    readonly property var plan:api.plan || ({})
    readonly property var bailian:api.bailian || ({baseUrl:"https://dashscope.aliyuncs.com",speechModel:"fun-asr",textModel:"qwen-plus"})
    property bool unifiedMode:true
    property bool advancedBailian:false
    property bool modelCorrection:false
    readonly property bool referenceMode:kind==="text" && !modelCorrection
    property string referenceTarget:""
    readonly property var providers:(api.providers || []).filter(function(row){return row.kind===panel.kind;})
    property string shownProvider:""
    property bool draftDirty:false
    readonly property bool editable: !!apiModel && !api.busy
    readonly property bool taskReady:editable && !!config.keyReady && !draftDirty && !api.offline && (!unifiedMode || !!api.usingBailian)
    function providerIndex(){for(var i=0;i<providers.length;i++)if(providers[i].id===config.provider)return i;return 0;}
    function loadFields(){baseField.text=config.baseUrl || "";modelField.text=config.model || "";budgetField.text=String(config.budgetPTC || 0);keyField.clear();shownProvider=config.provider || "";if(!unifiedMode)draftDirty=false;}
    function loadBailianFields(){bailianBase.text=bailian.baseUrl || "https://dashscope.aliyuncs.com";bailianSpeech.text=bailian.speechModel || "fun-asr";bailianText.text=bailian.textModel || "qwen-plus";bailianKey.clear();bailianRemember.checked=!!bailian.remembered;draftDirty=false;}
    onConfigChanged:if(config.provider!==shownProvider || !draftDirty)loadFields()
    onBailianChanged:if(!draftDirty)loadBailianFields()
    function loadReferenceFields(){referenceTitle.text=api.referenceQuery || "";referenceArtist.text=api.referenceArtist || "";referenceTarget=api.targetTitle || "";}
    onApiChanged:if(referenceTitle && referenceTarget!==(api.targetTitle || ""))loadReferenceFields()
    Component.onCompleted:{loadFields();loadBailianFields();loadReferenceFields();}

    component APIField: TextField {
        implicitHeight:38
        color:Theme.text; placeholderTextColor:Theme.muted; selectByMouse:true
        font.family:"PingFang SC"; font.pixelSize:13
        leftPadding:12; rightPadding:12
        background:Rectangle { radius:7; color:Theme.canvas; border.color:parent.activeFocus ? Theme.focus : Theme.stroke; border.width:parent.activeFocus ? 2 : 1 }
    }
    ScrollView {
        id:scroll
        anchors.fill:parent; anchors.leftMargin:Theme.pagePadding; anchors.rightMargin:Theme.pagePadding; anchors.topMargin:18; anchors.bottomMargin:8
        contentWidth:availableWidth; clip:true
        ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
        ColumnLayout {
            width:scroll.availableWidth; spacing:14
            RowLayout {
                Layout.fillWidth:true
                ColumnLayout {
                    Layout.fillWidth:true; spacing:6
                    Text { text:"百炼与歌词识别"; color:Theme.text; font.pixelSize:Theme.fontPageTitle; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; Accessible.role:Accessible.Heading; Accessible.name:text }
                    Text { Layout.fillWidth:true; text:panel.referenceMode ? "自动找原版歌词，对照识别文字纠错。沿用实际歌曲时间码，无需额外 Key。" : panel.unifiedMode ? "使用阿里云百炼识别歌曲、检查错字。已有 Key 可直接从第 2 步开始。" : "按功能独立配置服务商，结果审阅后再应用。"; color:Theme.secondary; font.pixelSize:13; wrapMode:Text.Wrap }
                }
                StatusPill { text:api.offline ? "离线演出 · 云请求暂停" : "手动调用"; tone:api.offline ? "amber" : "muted" }
            }
            RowLayout {
                Layout.fillWidth:true; spacing:8
                StudioButton { objectName:"apiSpeechTab"; text:"语音识别"; iconName:"audio"; selected:panel.kind==="speech"; onClicked:panel.kind="speech" }
                StudioButton { objectName:"apiTextTab"; text:"文字纠错"; iconName:"correct"; selected:panel.kind==="text"; onClicked:panel.kind="text" }
                Item { Layout.fillWidth:true }
                StudioButton { objectName:"apiIndependentMode"; visible:!panel.referenceMode; text:panel.unifiedMode ? "其他平台 / 独立配置" : "百炼统一配置"; enabled:panel.editable; onClicked:{panel.unifiedMode=!panel.unifiedMode;panel.draftDirty=false;panel.loadFields();panel.loadBailianFields();} }
            }
            RowLayout {
                visible:panel.kind==="text"; Layout.fillWidth:true; spacing:8
                StudioButton { objectName:"apiReferenceMode"; text:"原版歌词匹配"; selected:!panel.modelCorrection; enabled:panel.editable; onClicked:panel.modelCorrection=false }
                StudioButton { objectName:"apiModelCorrectionMode"; text:"模型文字检查"; selected:panel.modelCorrection; enabled:panel.editable; onClicked:panel.modelCorrection=true }
                Item { Layout.fillWidth:true }
            }
            // 歌暮式：三步放进一张卡片，靠底色和页面分开，不用分隔线
            Rectangle {
                objectName:"bailianStepsCard"
                visible:panel.unifiedMode && !panel.referenceMode; Layout.fillWidth:true
                implicitHeight:bailianSteps.implicitHeight + 36
                color:Theme.surface; radius:Theme.radiusCard
            ColumnLayout {
                id:bailianSteps
                anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                spacing:12
                StepHeader { number:1; title:"开通服务"; done:!!panel.bailian.keyReady }
                RowLayout {
                    Layout.fillWidth:true; spacing:10
                    ActionButton { objectName:"bailianOpenConsole"; text:"前往官网开通 / 充值"; tone:"primary"; enabled:!!panel.apiModel; hint:"在浏览器打开阿里云百炼北京控制台；充值在官网的费用与成本入口完成"; onClicked:panel.apiModel.openBailianPortal("activate") }
                    StudioButton { objectName:"bailianOpenKey"; text:"获取 API Key"; enabled:!!panel.apiModel; hint:"打开百炼北京地域的 API Key 管理页"; onClicked:panel.apiModel.openBailianPortal("key") }
                    StudioButton { objectName:"bailianOpenPricing"; text:"查看官方价格"; enabled:!!panel.apiModel; onClicked:panel.apiModel.openBailianPortal("pricing") }
                    Item { Layout.fillWidth:true }
                }
                Text { objectName:"bailianPurchaseGuide"; Layout.fillWidth:true; text:"登录阿里云并开通百炼，按需在官网充值；调用按用量计费。选择北京地域，创建通用 API Key，套餐专属 Key 不适用。"; color:Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap }
                Text { objectName:"bailianServiceScope"; Layout.fillWidth:true; text:"单首识别、曲库批量准备与文字检查，共用这一个百炼 Key。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                Text { objectName:"bailianPortalNotice"; visible:!!panel.api.portalNotice; Layout.fillWidth:true; text:panel.api.portalNotice || ""; color:panel.api.portalFallbackUrl ? Theme.amber : Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap; Accessible.role:Accessible.StaticText; Accessible.name:text }
                APIField { objectName:"bailianPortalFallback"; visible:!!panel.api.portalFallbackUrl; Layout.fillWidth:true; readOnly:true; text:panel.api.portalFallbackUrl || ""; Accessible.name:"官网地址，可选择复制" }
                RowLayout {
                    Layout.fillWidth:true; Layout.topMargin:10; spacing:12
                    StepHeader { number:2; title:"填入 Key"; done:!!panel.bailian.keyReady && !panel.draftDirty }
                    StatusPill { objectName:"bailianKeyStatus"; text:panel.draftDirty ? "有未应用的修改" : panel.bailian.keyReady ? (panel.api.usingBailian ? "Key 已填入" : "Key 已填入，待启用") : "尚未填写"; tone:panel.draftDirty ? "amber" : panel.bailian.keyReady ? "green" : "muted" }
                    Item { Layout.fillWidth:true }
                }
                RowLayout {
                    Layout.fillWidth:true; spacing:12
                    APIField { id:bailianKey; objectName:"bailianKeyField"; Layout.fillWidth:true; enabled:panel.editable; echoMode:bailianReveal.selected ? TextInput.Normal : TextInput.Password; placeholderText:panel.bailian.keyReady ? "已配置，留空保留" : "粘贴北京地域的通用 API Key"; Accessible.name:"百炼识别与纠错共用的API Key"; onTextEdited:panel.draftDirty=true }
                    StudioButton { id:bailianReveal; text:selected ? "隐藏" : "显示"; selected:false; enabled:panel.editable; onClicked:selected=!selected }
                    ActionButton { objectName:"bailianSaveConfig"; text:"使用这个 Key"; tone:"primary"; enabled:panel.editable && (bailianKey.text.trim().length>0 || !!panel.bailian.keyReady); onClicked:if(panel.apiModel.configureBailian(bailianKey.text,bailianBase.text,bailianSpeech.text,bailianText.text,bailianRemember.checked)){panel.loadFields();panel.loadBailianFields();bailianReveal.selected=false;} }
                }
                RowLayout {
                    Layout.fillWidth:true; spacing:12
                DarkCheckBox {
                    id:bailianRemember; objectName:"bailianRememberKey"
                    text:"在这台 Mac 上安全记住 Key（系统钥匙串）"
                    enabled:panel.editable; labelColor:Theme.text
                    onClicked:panel.draftDirty=true
                }
                Item { Layout.fillWidth:true }
                StudioButton { objectName:"bailianClearKey"; text:"清除 Key"; enabled:panel.editable && (!!panel.bailian.keyReady || !!panel.bailian.remembered); onClicked:{panel.apiModel.clearBailianKey();panel.loadBailianFields();} }
                StudioButton { objectName:"bailianAdvancedToggle"; text:panel.advancedBailian ? "收起高级设置" : "高级设置"; selected:panel.advancedBailian; onClicked:panel.advancedBailian=!panel.advancedBailian }
                }
                Text { objectName:"bailianStorageStatus"; Layout.fillWidth:true; text:panel.bailian.keychainNotice || (panel.bailian.remembered ? "已保存到系统钥匙串；清除 Key 会同时删除保存项。" : "当前未记住 Key。不勾选则仅本次运行使用；勾选后重启可恢复。"); color:panel.bailian.keychainNotice ? Theme.amber : Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                GridLayout {
                    visible:panel.advancedBailian; Layout.fillWidth:true; columns:2; columnSpacing:12; rowSpacing:10
                    Text { text:"北京接口主机"; color:Theme.secondary; font.pixelSize:13; Layout.preferredWidth:130 }
                    APIField { id:bailianBase; objectName:"bailianBaseField"; Layout.fillWidth:true; enabled:panel.editable; Accessible.name:"百炼北京接口主机"; onTextEdited:panel.draftDirty=true }
                    Text { text:"识别模型"; color:Theme.secondary; font.pixelSize:13 }
                    APIField { id:bailianSpeech; objectName:"bailianSpeechModel"; Layout.fillWidth:true; enabled:panel.editable; Accessible.name:"百炼文件识别模型"; onTextEdited:panel.draftDirty=true }
                    Text { text:"纠错模型"; color:Theme.secondary; font.pixelSize:13 }
                    APIField { id:bailianText; objectName:"bailianTextModel"; Layout.fillWidth:true; enabled:panel.editable; Accessible.name:"百炼文字纠错模型"; onTextEdited:panel.draftDirty=true }
                    Item { Layout.preferredWidth:130 }
                    Text { Layout.fillWidth:true; text:"默认 Fun-ASR + Qwen-Plus。可使用同一北京业务空间的专属主机；修改主机后需要填写该地址对应的 Key。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                }
                RowLayout {
                    Layout.fillWidth:true; Layout.topMargin:10; spacing:12
                    StepHeader { number:3; title:"检查连接"; done:/连接成功/.test(String(panel.bailian.textResult || "")) }
                    ActionButton { objectName:"bailianTestConnection"; implicitHeight:34; text:panel.api.busy ? "任务处理中…" : "检查连接"; enabled:panel.taskReady; hint:"核验上传权限，并发送一条短文字测试；不上传歌曲，文字测试可能产生少量费用"; onClicked:panel.apiModel.testBailian() }
                    Text { Layout.fillWidth:true; text:panel.api.offline ? "当前为离线演出，请先关闭离线模式。" : "不上传歌曲；短文字测试可能产生少量费用。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                }
                Text { objectName:"bailianCapabilities"; Layout.fillWidth:true; text:"歌词识别："+(panel.bailian.speechResult || "尚未验证")+"\n文字纠错："+(panel.bailian.textResult || "尚未验证"); color:Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap; lineHeight:1.5; Accessible.role:Accessible.StaticText; Accessible.name:text }
            }
            }
            GridLayout {
                visible:!panel.unifiedMode && !panel.referenceMode
                Layout.fillWidth:true; columns:2; columnSpacing:18; rowSpacing:12
                Text { text:"服务商"; color:Theme.secondary; font.pixelSize:13; Layout.preferredWidth:130 }
                DarkComboBox { objectName:"apiProviderCombo"; Layout.fillWidth:true; model:panel.providers; textRole:"label"; currentIndex:panel.providerIndex(); enabled:panel.editable; Accessible.name:"模型服务商"; onActivated:panel.apiModel.selectProvider(panel.kind,panel.providers[index].id) }
                Item { Layout.preferredWidth:130 }
                Text { Layout.fillWidth:true; text:panel.config.hint || "请通过开发版入口打开此设置页"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap; lineHeight:1.4 }
                Text { text:"模型 ID"; color:Theme.secondary; font.pixelSize:13 }
                APIField { id:modelField; objectName:"apiModelField"; Layout.fillWidth:true; placeholderText:"填写控制台中已开通的模型或接入点ID"; enabled:panel.editable; Accessible.name:"模型ID"; onTextEdited:panel.draftDirty=true }
                Text { text:"接口地址"; color:Theme.secondary; font.pixelSize:13 }
                APIField { id:baseField; objectName:"apiBaseField"; Layout.fillWidth:true; placeholderText:"Base URL，例如https://服务地址/v1"; enabled:panel.editable; Accessible.name:"模型接口Base URL"; onTextEdited:panel.draftDirty=true }
                Text { text:"API Key"; color:Theme.secondary; font.pixelSize:13 }
                RowLayout {
                    Layout.fillWidth:true; spacing:8
                    APIField { id:keyField; objectName:"apiKeyField"; Layout.fillWidth:true; echoMode:keyReveal.selected ? TextInput.Normal : TextInput.Password; placeholderText:panel.config.keyReady ? "已配置，留空保留" : "粘贴这个服务商的API Key"; enabled:panel.editable; Accessible.name:"API Key，密码输入"; onTextEdited:panel.draftDirty=true }
                    StudioButton { id:keyReveal; objectName:"apiKeyReveal"; text:selected ? "隐藏" : "显示"; selected:false; enabled:panel.editable; hint:"仅显示当前输入框中的Key，已保存的Key不会回填"; onClicked:selected=!selected }
                }
                Text { visible:panel.config.provider==="302-singing"; text:"本机额度"; color:Theme.secondary; font.pixelSize:13 }
                RowLayout {
                    visible:panel.config.provider==="302-singing"; Layout.fillWidth:true; spacing:10
                    APIField { id:budgetField; objectName:"apiBudgetField"; Layout.preferredWidth:140; enabled:panel.editable; validator:DoubleValidator { bottom:0;top:1000;decimals:4;notation:DoubleValidator.StandardNotation } Accessible.name:"302单次预算PTC"; onTextEdited:panel.draftDirty=true }
                    Text { text:"PTC，302平台单位"; color:Theme.secondary; font.pixelSize:12 }
                    Item { Layout.fillWidth:true }
                }
            }
            RowLayout {
                visible:!panel.unifiedMode && !panel.referenceMode
                Layout.fillWidth:true; spacing:10; Layout.leftMargin:148
                ActionButton {
                    objectName:"apiSaveConfig"; text:"保存配置"; tone:"primary"; enabled:panel.editable
                    onClicked:if(panel.apiModel.configure(panel.kind,panel.config.provider,baseField.text,modelField.text,keyField.text,Number(budgetField.text || 0))){panel.loadFields();keyReveal.selected=false;}
                }
                StudioButton { objectName:"apiTestConfig"; text:panel.kind==="speech" ? "核验配置" : "测试连接"; enabled:panel.editable && !!panel.config.keyReady && !panel.draftDirty && !panel.api.offline; hint:panel.kind==="speech" ? "只核验本机配置，不上传音频" : "向所选模型发送短测试文本，可能产生少量用量"; onClicked:panel.apiModel.test(panel.kind) }
                StudioButton { objectName:"apiClearKey"; text:"清除 Key"; enabled:panel.editable && !!panel.config.keyReady; onClicked:panel.apiModel.clearKey(panel.kind) }
                Item { Layout.fillWidth:true }
                Text { text:panel.config.remembered ? "已记住在系统钥匙串" : panel.config.keyReady ? "Key仅用于本次运行" : "尚未配置Key"; color:panel.config.keyReady ? Theme.green : Theme.muted; font.pixelSize:12 }
            }
            Text { objectName:"apiConnectionResult"; visible:!panel.unifiedMode && !panel.referenceMode; Layout.fillWidth:true; Layout.leftMargin:148; text:panel.config.result || "尚未验证"; color:Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap; Accessible.role:Accessible.StaticText; Accessible.name:text }
            StudioButton { objectName:"apiImport302"; visible:!panel.unifiedMode && !panel.referenceMode; text:"导入旧302配置"; enabled:panel.editable; onClicked:panel.apiModel.import302() }
            Rectangle { Layout.fillWidth:true; Layout.preferredHeight:1; Layout.topMargin:6; color:Theme.stroke }
            RowLayout {
                Layout.fillWidth:true; spacing:10
                Text { text:panel.kind==="speech" ? "识别哪首歌" : "纠错哪份歌词"; color:Theme.text; font.pixelSize:16; font.weight:Font.Medium }
                DarkComboBox { objectName:"apiScopeCombo"; Layout.preferredWidth:220; model:["当前制作歌曲","A盘当前歌曲","B盘当前歌曲"]; currentIndex:panel.api.scope==="A" ? 1 : panel.api.scope==="B" ? 2 : 0; enabled:panel.editable; Accessible.name:"AI处理的目标歌曲"; onActivated:panel.apiModel.setScope(index===0 ? "studio" : index===1 ? "A" : "B") }
                Item { Layout.fillWidth:true }
                ActionButton { objectName:"apiPlanSpeech"; visible:panel.kind==="speech"; text:"预检歌曲与上传计划"; enabled:panel.taskReady; onClicked:panel.apiModel.planSpeech() }
                ActionButton { objectName:"apiCorrectLyrics"; visible:panel.kind==="text" && panel.modelCorrection; text:"生成模型建议"; tone:"primary"; enabled:panel.taskReady; hint:"仅发送歌词文字；模型未查找原版歌词，调用按服务账户计费"; onClicked:panel.apiModel.correctLyrics() }
            }
            Text { Layout.fillWidth:true; text:panel.kind==="speech" ? "预检在本机完成，不上传。确认计划后才发送完整音频，费用按服务账户实际用量结算。" : panel.referenceMode ? "搜索只发送歌名和歌手；匹配在本机完成。原版时码不会写入 Remix，截断采样、循环和口播保留识别原文。" : "只发送歌词文字给模型；建议来自语言模型推断，未搜索原版歌词。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
            Text { objectName:"apiTargetTitle"; Layout.fillWidth:true; text:"当前歌曲："+(panel.api.targetTitle || "尚未选择"); textFormat:Text.PlainText; color:Theme.text; font.pixelSize:13; elide:Text.ElideMiddle; Accessible.role:Accessible.StaticText; Accessible.name:text }
            ColumnLayout {
                visible:panel.referenceMode; Layout.fillWidth:true; spacing:12
                GridLayout {
                    Layout.fillWidth:true; columns:2; columnSpacing:12; rowSpacing:8
                    Text { text:"原版歌名"; color:Theme.secondary; font.pixelSize:13 }
                    APIField { id:referenceTitle; objectName:"apiReferenceTitle"; Layout.fillWidth:true; enabled:panel.editable; placeholderText:"从文件名提取，可修改"; maximumLength:120; Accessible.name:"搜索原版歌词的歌名" }
                    Text { text:"歌手（可选）"; color:Theme.secondary; font.pixelSize:13 }
                    APIField { id:referenceArtist; objectName:"apiReferenceArtist"; Layout.fillWidth:true; enabled:panel.editable; placeholderText:"重名时填写原唱歌手"; maximumLength:120; Accessible.name:"原唱歌手，可选" }
                }
                RowLayout {
                    Layout.fillWidth:true
                    ActionButton { objectName:"apiSearchReference"; text:panel.api.offline ? "用本地参考匹配" : "自动找歌词并匹配"; tone:"primary"; enabled:panel.editable && referenceTitle.text.trim().length>0; onClicked:panel.apiModel.searchReference(referenceTitle.text,referenceArtist.text) }
                    Text { text:panel.api.offline ? "仅使用已缓存的原版歌词" : "网易云音乐 · 无需 Key"; color:Theme.muted; font.pixelSize:12 }
                    Item { Layout.fillWidth:true }
                }
                Text { visible:(panel.api.references || []).length>0; text:panel.api.referenceCached ? "参考歌词已从本地缓存读取" : "找到以下参考版本，可切换后重新匹配"; color:Theme.secondary; font.pixelSize:12 }
                Repeater {
                    model:panel.api.references || []
                    delegate:RowLayout {
                        required property var modelData
                        Layout.fillWidth:true; spacing:12
                        ColumnLayout {
                            Layout.fillWidth:true; spacing:4
                            Text { Layout.fillWidth:true; text:modelData.title+" · "+modelData.artist; textFormat:Text.PlainText; color:Theme.text; font.pixelSize:14; wrapMode:Text.Wrap }
                            Text { Layout.fillWidth:true; text:modelData.providerLabel+" · 文字匹配 "+Math.round(modelData.coverage*100)+"% · "+modelData.matchedRows+" / "+modelData.totalRows+" 句有锚点"; textFormat:Text.PlainText; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                        }
                        StudioButton { objectName:"apiReferenceCandidate-"+modelData.id; text:"用这版匹配"; Accessible.name:"用 "+modelData.artist+" 的 "+modelData.title+" 匹配"; enabled:panel.editable; onClicked:panel.apiModel.matchReference(modelData.id) }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth:true; spacing:10
                Text { objectName:"apiTaskStatus"; Layout.fillWidth:true; text:panel.api.detail || ""; textFormat:Text.PlainText; color:panel.api.phase==="failed" ? Theme.red : Theme.secondary; font.pixelSize:13; wrapMode:Text.Wrap; Accessible.role:Accessible.StaticText; Accessible.name:text }
                StudioButton { objectName:"apiCancelTask"; text:"取消任务"; visible:!!panel.api.busy; onClicked:panel.apiModel.cancel() }
                StudioButton { objectName:"apiQuerySpeech"; text:"继续查询原任务"; visible:panel.kind==="speech" && !!panel.api.canQuery; enabled:panel.editable && !panel.api.offline; onClicked:panel.apiModel.querySpeech() }
            }
            ColumnLayout {
                visible:!!panel.plan.planId && panel.kind==="speech"; Layout.fillWidth:true; spacing:8
                Text { Layout.fillWidth:true; text:"上传歌曲："+(panel.plan.sourcePath || ""); color:Theme.text; font.pixelSize:13; wrapMode:Text.WrapAnywhere }
                Text { Layout.fillWidth:true; text:"服务："+((panel.plan.config || {}).provider || "")+" / "+((panel.plan.config || {}).model || "")+" · 时长 "+(Number(panel.plan.durationMs || 0)/1000).toFixed(1)+" 秒 · "+(Number(panel.plan.size || 0)/1048576).toFixed(2)+" MB"; color:Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap }
                Text { Layout.fillWidth:true; text:"实际版本："+(panel.plan.versionId || ""); color:Theme.muted; font.pixelSize:11; wrapMode:Text.WrapAnywhere }
                Text { visible:panel.config.provider==="302-singing"; text:"302预估："+Number(panel.plan.estimatedCostPTC || 0).toFixed(4)+" PTC；这是本机预估，不是账户余额。"; color:Theme.amber; font.pixelSize:12 }
                Text { visible:panel.plan.estimatedCostCNY!==undefined && panel.plan.estimatedCostCNY!==null; Layout.fillWidth:true; text:"识别预估：约 ¥"+Number(panel.plan.estimatedCostCNY || 0).toFixed(4)+"，按整段时长估算，实际费用由百炼结算。"; color:Theme.amber; font.pixelSize:12; wrapMode:Text.Wrap }
                Text { visible:panel.config.provider==="bailian-asr"; Layout.fillWidth:true; text:"确认后将歌曲上传至百炼私有临时存储，文件有效期48小时。若已有原任务，将继续查询；识别结果需审阅后应用。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                ActionButton { objectName:"apiStartSpeech"; text:"确认上传并识别"; tone:"primary"; enabled:panel.taskReady; onClicked:panel.apiModel.startSpeech() }
            }
            ColumnLayout {
                visible:!!panel.api.hasProposal; Layout.fillWidth:true; spacing:12
                Text { text:panel.api.proposalKind==="correct" ? "逐句审阅纠错建议" : "识别结果"; color:Theme.text; font.pixelSize:18; font.weight:Font.Medium }
                Text { objectName:"apiReferenceProof"; visible:!!panel.api.referenceProposal; Layout.fillWidth:true; text:"参考："+((panel.api.reference || {}).title || "")+" · "+((panel.api.reference || {}).artist || "")+" / "+((panel.api.reference || {}).providerLabel || "")+"\n"+((panel.api.reference || {}).url || "")+"\n文字匹配 "+Math.round(panel.api.referenceCoverage*100)+"%，不代表听音准确率。修改保留实际句时码；字数变化的建议需试听确认。"; textFormat:Text.PlainText; color:Theme.secondary; font.pixelSize:12; wrapMode:Text.Wrap; Accessible.role:Accessible.StaticText; Accessible.name:text }
                Text { visible:!!panel.api.referenceProposal && !(panel.api.changes || []).length; Layout.fillWidth:true; text:"未发现可确认的错字；未匹配片段保留原文。"; color:Theme.secondary; font.pixelSize:13; wrapMode:Text.Wrap }
                Text { visible:!!panel.api.proposalStale; Layout.fillWidth:true; text:"歌曲或歌词已改变，此结果不能覆盖当前内容。请重新生成建议或预检。"; color:Theme.amber; font.pixelSize:13; wrapMode:Text.Wrap }
                Repeater {
                    model:panel.api.proposalKind==="correct" ? panel.api.changes || [] : []
                    delegate:ColumnLayout {
                        required property var modelData
                        Layout.fillWidth:true; spacing:6
                        RowLayout {
                            Layout.fillWidth:true; spacing:10
                            DarkCheckBox { objectName:"apiChange-"+modelData.index; text:"第 "+(modelData.index+1)+" 句"; checked:modelData.selected; enabled:panel.editable; palette.windowText:Theme.text; onClicked:panel.apiModel.selectChange(modelData.index,checked) }
                            ColumnLayout { Layout.fillWidth:true; spacing:5
                                Text { Layout.fillWidth:true; text:"原文："+modelData.before; textFormat:Text.PlainText; color:Theme.secondary; font.pixelSize:13; wrapMode:Text.Wrap }
                                Text { Layout.fillWidth:true; text:"建议："+modelData.after; textFormat:Text.PlainText; color:modelData.selected ? Theme.accent : Theme.muted; font.pixelSize:13; wrapMode:Text.Wrap }
                                Text { visible:!!modelData.referenceText; Layout.fillWidth:true; text:"对照："+(modelData.referenceText || ""); textFormat:Text.PlainText; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
                                Text { visible:!!modelData.timingNote; Layout.fillWidth:true; text:modelData.timingNote || ""; textFormat:Text.PlainText; color:modelData.timingPreserved ? Theme.muted : Theme.amber; font.pixelSize:12; wrapMode:Text.Wrap }
                            }
                        }
                        Rectangle { Layout.fillWidth:true; Layout.preferredHeight:1; color:Theme.stroke }
                    }
                }
                ColumnLayout {
                    visible:!!panel.api.referenceProposal && (panel.api.unresolved || []).length>0; Layout.fillWidth:true; spacing:7
                    Text { text:"保留原文 · 建议试听核对"; color:Theme.secondary; font.pixelSize:14; font.weight:Font.Medium }
                    Repeater { model:panel.api.unresolved || []; delegate:Text { required property var modelData; Layout.fillWidth:true; text:"第 "+(modelData.index+1)+" 句："+modelData.reason; textFormat:Text.PlainText; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap } }
                }
                ScrollView {
                    visible:panel.api.proposalKind==="speech"; Layout.fillWidth:true; Layout.preferredHeight:190; clip:true
                    TextArea { objectName:"apiTranscriptPreview"; text:panel.api.previewText || ""; readOnly:true; selectByMouse:true; wrapMode:TextEdit.Wrap; color:Theme.text; font.pixelSize:14; background:Rectangle { color:Theme.surface; radius:7 } }
                }
                RowLayout {
                    Layout.fillWidth:true; spacing:10
                    ActionButton { objectName:"apiApplyProposal"; text:panel.api.proposalKind==="correct" ? "应用勾选的修改" : "应用识别时间轴"; tone:"primary"; enabled:panel.editable && !!panel.api.canApply; onClicked:panel.apiModel.apply() }
                    StudioButton { objectName:"apiSaveTranscript"; text:"保存参考文字"; visible:panel.api.proposalKind==="speech"; enabled:panel.editable; onClicked:panel.apiModel.saveTranscript() }
                    StudioButton { objectName:"apiDiscardProposal"; text:"放弃结果"; enabled:panel.editable; onClicked:panel.apiModel.discard() }
                    Item { Layout.fillWidth:true }
                    Text { visible:panel.api.proposalKind==="speech" && !panel.api.canApply; text:"缺少时间码，需手动对齐"; color:Theme.amber; font.pixelSize:12 }
                }
            }
            Text { Layout.fillWidth:true; text:"服务地址和模型配置保存在本机；Key不写入工程、任务文件或配置JSON。离线演出会暂停云请求。"; color:Theme.muted; font.pixelSize:11; wrapMode:Text.Wrap; Layout.topMargin:12; Layout.bottomMargin:12 }
        }
    }
}
