import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: library
    property var stateData: ({})
    property string selectedCategory: "全部"
    signal adjustRequested()
    readonly property var settings: stateData.style || ({})
    readonly property var entries: controller ? controller.stylePresets || [] : []
    readonly property var categories: {
        var result = ["全部"]
        for (var i = 0; i < entries.length; i++)
            if (result.indexOf(entries[i].category) < 0) result.push(entries[i].category)
        return result
    }
    readonly property var filteredPresets: {
        var query = search.text.trim().toLowerCase()
        return entries.filter(function (entry) {
            return (selectedCategory === "全部" || entry.category === selectedCategory)
                && (!query || (entry.name + " " + entry.detail + " " + entry.category).toLowerCase().indexOf(query) >= 0)
        })
    }
    readonly property var activePreset: {
        for (var i = 0; i < entries.length; i++)
            if (entries[i].id === settings.presetId) return entries[i]
        return null
    }
    readonly property int paletteCount: {
        var count = 0
        for (var i = 0; i < entries.length; i++) count += entries[i].schemes.length
        return count
    }
    function cardState(entry) {
        var appearance = Object.assign({}, entry.previewStyle)
        appearance.fontSize = 72
        appearance.verticalPosition = 34
        appearance.horizontalMargin = 8
        appearance.lineMode = "single"
        appearance.outputMode = "standard"
        appearance.dynamicBackground = true
        return {selectedSource: "A", effectiveSource: "A", stale: false, outputBlocked: false,
            inputMode: "style-preview", scene: "amber", deckA: {cacheState: "ready"},
            style: appearance, lyric: {visible: true, text: "让旋律照亮此刻", nextText: "一起唱出心中的歌",
                quality: "sentence", progress: 0.48}}
    }
    function resetFilters() { selectedCategory="全部"; search.text=""; }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text { text: library.entries.length + " 组样式 · " + library.paletteCount + " 套配色"; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            TextField {
                id: search
                objectName: "style-presetSearch"
                Layout.preferredWidth: Math.min(260, library.width * .4)
                Layout.preferredHeight: 36
                placeholderText: "搜索样式，如樱花、HUD"
                Accessible.name: "搜索字幕预设"
                selectByMouse: true
                color: Theme.text
                placeholderTextColor: Theme.muted
                font.pixelSize: 12
                leftPadding: 12
                rightPadding: 12
                background: Rectangle { radius: 8; color: Theme.surface; border.color: search.activeFocus ? Theme.focus : Theme.stroke }
            }
        }
        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: childrenRect.height
            spacing: 6
            Repeater {
                model: library.categories
                ActionButton {
                    required property string modelData
                    text: modelData
                    implicitHeight: 30
                    font.pixelSize: 11
                    padding: 10
                    selected: library.selectedCategory === modelData
                    onClicked: library.selectedCategory = modelData
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: paletteControls.implicitHeight + 24
            color: Theme.surface
            border.color: Theme.stroke
            radius: 12
            RowLayout {
                id: paletteControls
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8
                Text {
                    objectName: "style-activePresetName"
                    Layout.preferredWidth: library.activePreset ? 160 : 270
                    text: library.activePreset ? library.activePreset.name + (library.settings.presetCustomized ? " · 已微调" : "") : "选择下方样式，立即预览并应用"
                    color: Theme.text
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Flow {
                    visible: !!library.activePreset
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? childrenRect.height : 0
                    spacing: 8
                    Repeater {
                        model: library.activePreset ? library.activePreset.schemes : []
                        Button {
                            id: schemeButton
                            required property var modelData
                            required property int index
                            objectName: "style-scheme-" + index
                            text: modelData.name || "配色 " + (index + 1)
                            Accessible.name: (library.activePreset ? library.activePreset.name : "") + " " + text
                            property bool selected: Number(library.settings.schemeIndex || 0) === index
                            implicitWidth: 122
                            implicitHeight: 32
                            hoverEnabled: true
                            onClicked: controller.setStylePresetScheme(library.activePreset.id, index)
                            background: Rectangle { radius: 7; color: schemeButton.selected ? Theme.accentSoft : Theme.elevated; border.color: schemeButton.selected || schemeButton.activeFocus ? Theme.focus : Theme.stroke; border.width: schemeButton.selected ? 2 : 1 }
                            contentItem: Row {
                                spacing: 6
                                anchors.centerIn: parent
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 2
                                    Repeater {
                                        model: [schemeButton.modelData.bg, schemeButton.modelData.fg, schemeButton.modelData.accent, schemeButton.modelData.accent2]
                                        Rectangle { required property var modelData; width: 9; height: 15; radius: 2; color: modelData || "#FFFFFF"; border.color: "#667080"; border.width: .5 }
                                    }
                                }
                                Text { text: schemeButton.text; anchors.verticalCenter: parent.verticalCenter; color: schemeButton.selected ? Theme.accent : Theme.secondary; font.pixelSize: 11 }
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true; visible: !library.activePreset }
                ActionButton { text: "微调此样式"; implicitHeight: 30; onClicked: library.adjustRequested() }
            }
        }
        GridView {
            id: grid
            objectName: "style-presetGrid"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: library.filteredPresets
            readonly property int columnCount: Math.max(2, Math.floor((width - 12) / 220))
            cellWidth: (width - 12) / columnCount
            cellHeight: Math.max(120, Math.min(194, cellWidth * .52 + 72, height - 6))
            reuseItems: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Button {
                id: card
                required property var modelData
                required property int index
                objectName: "style-card-" + modelData.id
                width: grid.cellWidth - 10
                height: grid.cellHeight - 10
                hoverEnabled: true
                Accessible.name: modelData.name + "，" + modelData.schemes.length + " 套配色"
                property bool selected: library.settings.presetId === modelData.id
                padding: 8
                onClicked: controller.setStylePreset(modelData.id)
                background: Rectangle {
                    radius: 12
                    color: card.hovered ? Theme.elevated : Theme.surface
                    border.color: card.selected || card.activeFocus ? Theme.focus : Theme.stroke
                    border.width: card.selected || card.activeFocus ? 2 : 1
                }
                contentItem: ColumnLayout {
                    spacing: 7
                    ProgramView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 68
                        compact: true
                        presetThumbnail: true
                        radius: 7
                        stateData: library.cardState(card.modelData)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: card.modelData.name; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        Text { text: card.selected ? "已应用" : card.modelData.schemes.length + " 配色"; color: card.selected ? Theme.accent : Theme.muted; font.pixelSize: 10 }
                    }
                    Text { Layout.fillWidth: true; text: card.modelData.detail; color: Theme.secondary; font.pixelSize: 10; elide: Text.ElideRight }
                }
            }
            Text { anchors.centerIn: parent; visible: grid.count === 0; text: "没有找到匹配样式，试试其他关键词"; color: Theme.secondary; font.pixelSize: 13 }
        }
    }
}
