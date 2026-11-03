import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: style
    property var studioModel: null
    property var stateData: ({})
    property string page: "font"
    property bool advancedExpanded: false
    readonly property var settings: stateData.settings || ({})
    readonly property var layout: (stateData.layouts || []).filter(function(row){return row.id===(settings.layoutId || "standard");})[0] || ({})
    readonly property bool editable: !!studioModel && !stateData.busy
    contentWidth: availableWidth; clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    function set(key,value) { if(editable) studioModel.setSetting(key,value); }
    function openEffects() {
        advancedExpanded=false;
        templateGallery.category="全部";
        if(contentItem) contentItem.contentY=0;
    }
    ColumnLayout {
        width: style.availableWidth; spacing: 10
        ColumnLayout {
            visible: style.page==="font"; Layout.fillWidth: true; spacing: 10
            Text { text: "歌词字体"; color: Theme.secondary; font.pixelSize: 10 }
            DarkComboBox { Layout.fillWidth: true; implicitHeight: 31; model: ["苹方", "黑体", "宋体"]; currentIndex: Math.max(0,["PingFang SC","Heiti SC","Songti SC"].indexOf(style.settings.fontFamily || "PingFang SC")); enabled: style.editable; Accessible.name: "歌词字体"; onActivated: function(index) { style.set("fontFamily",["PingFang SC","Heiti SC","Songti SC"][index]) } }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                StudioValueField { Layout.fillWidth: true; label: "字号"; fieldName: "styleFontSize"; value: Number(style.settings.fontSize || 42); minimum: 16; maximum: 150; decimals: 0; enabled: style.editable; onCommitted: function(value) { style.set("fontSize",value); } }
                StudioValueField { Layout.fillWidth: true; label: "描边"; fieldName: "styleStrokeWidth"; value: Number(style.settings.strokeWidth || 0); maximum: 12; decimals: 1; enabled: style.editable; onCommitted: function(value) { style.set("strokeWidth",value); } }
            }
            RowLayout { Layout.fillWidth: true; DarkCheckBox { text: "加粗"; checked: !!style.settings.fontBold; enabled: style.editable; onToggled: style.set("fontBold",checked) } DarkCheckBox { text: "阴影"; checked: !!style.settings.shadow; enabled: style.editable; onToggled: style.set("shadow",checked) } }
            Repeater {
                model: [{key:"textColor",label:"歌词颜色",fallback:"#F1F3F6"},{key:"highlightColor",label:"高亮颜色",fallback:"#A4C8FF"},{key:"strokeColor",label:"描边颜色",fallback:"#101216"}]
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: 5
                    Text { text: modelData.label; color: Theme.secondary; font.pixelSize: 10 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 7
                        Repeater {
                            id: colorSwatches
                            property string targetKey: modelData.key
                            model: ["#F0EFF4","#FFD890","#9EE2BF","#B4A0FF","#EEAEBB","#18181C"]
                            delegate: Button {
                                required property string modelData
                                objectName: "styleColor-"+colorSwatches.targetKey+"-"+modelData
                                width: 24; implicitWidth: 24; implicitHeight: 24; hoverEnabled: true; activeFocusOnTab: true
                                Accessible.name: "设置颜色 "+modelData
                                onClicked: style.set(colorSwatches.targetKey,modelData)
                                background: Rectangle { radius: 12; color: parent.modelData; border.color: parent.activeFocus || parent.hovered ? Theme.text : Theme.stroke; border.width: 2 }
                                contentItem: Item {}
                            }
                        }
                    }
                    TextField { objectName: "style-"+modelData.key; Layout.fillWidth: true; implicitHeight: 31; text: style.settings[modelData.key] || modelData.fallback; color: Theme.text; selectByMouse: true; enabled: style.editable; Accessible.name: modelData.label; validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ } onEditingFinished: if(acceptableInput) style.set(modelData.key,text); background: Rectangle { radius: 8; color: Theme.canvas; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
                }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                StudioValueField { Layout.fillWidth: true; label: "字间距"; fieldName: "styleLetterSpacing"; value: Number(style.settings.letterSpacing || 0); maximum: 20; decimals: 1; enabled: style.editable; onCommitted: function(value) { style.set("letterSpacing",value); } }
                StudioValueField { Layout.fillWidth: true; label: "行间距"; fieldName: "styleLineSpacing"; value: Number(style.settings.lineSpacing || 1); minimum: 0.8; maximum: 2; decimals: 2; enabled: style.editable; onCommitted: function(value) { style.set("lineSpacing",value); } }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                StudioValueField { Layout.fillWidth: true; label: "水平位置"; unit: "%"; fieldName: "styleX"; value: Number(style.settings.x || 0)*100; maximum: 100; decimals: 1; enabled: style.editable; onCommitted: function(value) { style.set("x",value/100); } }
                StudioValueField { Layout.fillWidth: true; label: "垂直位置"; unit: "%"; fieldName: "styleY"; value: Number(style.settings.y || 0)*100; maximum: 100; decimals: 1; enabled: style.editable; onCommitted: function(value) { style.set("y",value/100); } }
            }
        }
        ColumnLayout {
            visible: style.page==="template"; Layout.fillWidth: true; spacing: 10
            StudioCatalogPicker { visible: style.advancedExpanded; Layout.fillWidth: true; label: "按名称查找模板"; controlName: "styleTemplate"; rows: style.stateData.templates || []; selectedId: style.settings.templateId || "none"; enabled: style.editable; onChosen: function(id) { style.set("templateId",id); } }
            StudioCatalogPicker { Layout.fillWidth: true; label: "文字动画"; controlName: "styleEffect"; rows: style.stateData.effects || []; selectedId: style.settings.effectId || "none"; enabled: style.editable; onChosen: function(id) { style.set("effectId",id); } }
            StudioButton { objectName: "styleAdvancedToggle"; Layout.fillWidth: true; text: style.advancedExpanded ? "收起高级动画设置 ▴" : "高级动画设置 ▾"; selected: style.advancedExpanded; onClicked: style.advancedExpanded=!style.advancedExpanded }
            StudioTemplateGallery { id:templateGallery; visible: !style.advancedExpanded; studioModel: style.studioModel; stateData: style.stateData }
            StudioCatalogPicker { visible: style.advancedExpanded; Layout.fillWidth: true; label: "动态布局"; controlName: "styleLayout"; rows: style.stateData.layouts || []; selectedId: style.settings.layoutId || "standard"; enabled: style.editable; onChosen: function(id) { style.set("layoutId",id); } }
            Text { visible: style.advancedExpanded; text: "当前布局参数"; color: Theme.text; font.pixelSize: 11; font.weight: Font.Medium }
            GridLayout { visible: style.advancedExpanded; Layout.fillWidth: true; columns: 2; columnSpacing: 8; rowSpacing: 10
                Repeater {
                model: [{key:"offsetX",label:"水平偏移",min:-1,max:1,base:0},{key:"offsetY",label:"垂直偏移",min:-1,max:1,base:0},{key:"amplitude",label:"运动幅度",min:0,max:0.5,base:0},{key:"frequency",label:"频率",min:0,max:10,base:1},{key:"phase",label:"相位",min:-100,max:100,base:0},{key:"scale",label:"缩放",min:0.25,max:3,base:1},{key:"rotation",label:"旋转角度",min:-180,max:180,base:0}]
                delegate: StudioValueField {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true; Layout.columnSpan: index===6 ? 2 : 1
                    label: modelData.label; fieldName: "styleLayout-"+modelData.key
                    value: (style.settings.layoutParameters || {})[modelData.key]!==undefined ? Number(style.settings.layoutParameters[modelData.key]) : (style.layout.parameters || {})[modelData.key]!==undefined ? Number(style.layout.parameters[modelData.key]) : modelData.base
                    minimum: modelData.min; maximum: modelData.max; decimals: 3; enabled: style.editable
                    onCommitted: function(value) { var parameters=Object.assign({},style.settings.layoutParameters || {}); parameters[modelData.key]=value; style.set("layoutParameters",parameters); }
                }
            }
            }
            Text { Layout.fillWidth: true; visible: style.advancedExpanded; text: "位置和字体在“字体”页设置。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
            StudioValueField { visible: style.advancedExpanded; Layout.fillWidth: true; label: "淡入 / 淡出时长"; unit: "秒"; fieldName: "styleFade"; value: Number(style.settings.fadeMs || 0)/1000; maximum: 2; enabled: style.editable; onCommitted: function(value) { style.set("fadeMs",value*1000); } }
            StudioCatalogPicker { visible: style.advancedExpanded; Layout.fillWidth: true; label: "入场动画"; controlName: "styleEntrance"; rows: style.stateData.effects || []; selectedId: style.settings.entranceId || "none"; enabled: style.editable; onChosen: function(id) { style.set("entranceId",id); } }
            StudioCatalogPicker { visible: style.advancedExpanded; Layout.fillWidth: true; label: "离场动画"; controlName: "styleExit"; rows: style.stateData.effects || []; selectedId: style.settings.exitId || "none"; enabled: style.editable; onChosen: function(id) { style.set("exitId",id); } }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
