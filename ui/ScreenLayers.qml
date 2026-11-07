import QtQuick
import "ScreenLayerStyles.js" as Styles

Item {
    id: layers
    property var stateData: ({})
    readonly property var config: stateData.screenLayers || ({})
    readonly property var textLayer: config.text || ({})
    readonly property var logoLayer: config.logo || ({})
    readonly property real scale: width / 1120
    readonly property real margin: Math.min(width, height) * .045
    readonly property var textStyle: Styles.find(Styles.text, textLayer.style)
    readonly property var logoStyle: Styles.find(Styles.logo, logoLayer.style)
    readonly property real textPadding: textStyle.plate ? 26 * scale : 0
    readonly property real textInset: textStyle.art && vertical(textLayer.position) === 0 ? Math.min(height * .15, 84 * scale) : 0
    readonly property real lyricReserve: (textStyle.art || textStyle.plate) && vertical(textLayer.position) === 0 ? height * .46 : 0
    readonly property real logoPadding: logoStyle.plate ? 9 * scale : 0
    function placement(length, extent, alignment, inset) {
        return alignment === 0 ? inset : alignment === 2 ? length - extent - inset : (length - extent) / 2;
    }
    function horizontal(position) {
        return position && position.indexOf("left") >= 0 ? 0 : position && position.indexOf("right") >= 0 ? 2 : 1;
    }
    function vertical(position) {
        return position && position.indexOf("top") >= 0 ? 0 : position && position.indexOf("bottom") >= 0 ? 2 : 1;
    }
    Image {
        objectName: "screenTextScenery"
        visible: headline.visible && status === Image.Ready
        source: headline.visible ? (layers.textStyle.art || "") : ""
        width: layers.width * (layers.textStyle.artWidth || 0)
        height: Math.min(layers.height * .50, width * .35)
        x: (layers.width - width) / 2
        y: layers.placement(layers.height, height, layers.vertical(layers.textLayer.position), layers.margin * .25)
        fillMode: Image.PreserveAspectFit
        opacity: headline.opacity
        asynchronous: true
    }
    Rectangle {
        objectName: "screenLogoPlate"
        visible: logo.visible && !!layers.logoStyle.plate
        x: logo.x - layers.logoPadding; y: logo.y - layers.logoPadding
        width: logo.width + 2 * layers.logoPadding; height: logo.height + 2 * layers.logoPadding
        radius: 0
        color: layers.logoStyle.plate || "transparent"
        opacity: logo.opacity
        Rectangle {
            visible: layers.logoStyle.key === "matte"
            anchors.left: parent.left; anchors.bottom: parent.bottom
            width: parent.width; height: Math.max(1, layers.scale)
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#00C7D0DE" }
                GradientStop { position: .36; color: "#E1E7EF" }
                GradientStop { position: 1; color: "#00C7D0DE" }
            }
        }
        Rectangle {
            visible: layers.logoStyle.key === "foil"
            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            width: Math.max(1, 2 * layers.scale)
            gradient: Gradient {
                GradientStop { position: 0; color: "#00F87557" }
                GradientStop { position: .52; color: "#FFF87557" }
                GradientStop { position: 1; color: "#00F87557" }
            }
        }
    }
    Image {
        id: logo
        objectName: "screenLogoImage"
        source: layers.stateData.logoImageUrl || ""
        visible: !!layers.logoLayer.visible && status === Image.Ready
        width: layers.width * (layers.logoLayer.size || 18) / 100
        height: Math.max(0, Math.min(layers.height - 2 * (layers.margin + layers.logoPadding), width * sourceSize.height / Math.max(1, sourceSize.width)))
        x: layers.placement(layers.width, width, layers.horizontal(layers.logoLayer.position), layers.margin + layers.logoPadding)
        y: layers.placement(layers.height, height, layers.vertical(layers.logoLayer.position), layers.margin + layers.logoPadding)
        opacity: layers.logoLayer.opacity === undefined ? 1 : layers.logoLayer.opacity
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        mipmap: true
    }
    Rectangle {
        objectName: "screenTextPlate"
        visible: headline.visible && (!!layers.textStyle.plate || !!layers.textStyle.art) && headline.paintedWidth > 0
        readonly property real paintedW: headline.paintedWidth * headline.scale
        readonly property real paintedH: headline.paintedHeight * headline.scale
        x: layers.placement(layers.width, width, layers.horizontal(layers.textLayer.position), layers.margin)
        y: layers.textInset + layers.placement(layers.height - layers.textInset, height, layers.vertical(layers.textLayer.position), layers.margin)
        width: paintedW + 2 * layers.textPadding
        height: paintedH + 2 * layers.textPadding
        color: layers.textStyle.plate || "transparent"
        opacity: headline.opacity
        Rectangle {
            visible: layers.textStyle.key === "record"
            anchors.left: parent.left; anchors.bottom: parent.bottom
            width: parent.width * .30; height: Math.max(1, 5 * layers.scale)
            color: layers.textStyle.accent || "transparent"
        }
    }
    Text {
        id: headline
        objectName: "screenTextLayer"
        anchors.fill: parent
        anchors.margins: layers.margin + layers.textPadding
        anchors.topMargin: layers.margin + layers.textPadding + layers.textInset
        anchors.bottomMargin: layers.margin + layers.textPadding + layers.lyricReserve
        visible: !!layers.textLayer.visible
        text: layers.textLayer.text || ""
        textFormat: Text.PlainText
        color: layers.textLayer.color || "#FFFFFF"
        opacity: layers.textLayer.opacity === undefined ? 1 : layers.textLayer.opacity
        font.family: "PingFang SC"
        font.weight: layers.textStyle.fontWeight || Font.Bold
        font.pixelSize: Math.max(1, (layers.textLayer.fontSize || 48) * layers.scale)
        minimumPixelSize: Math.max(1, 8 * layers.scale)
        fontSizeMode: Text.Fit
        // Text.Fit stops at its minimum font size. Fit exceptionally many
        // explicit lines as a whole, using the selected alignment as origin.
        scale: Math.min(1, width / Math.max(1, paintedWidth), height / Math.max(1, paintedHeight))
        transformOrigin: layers.vertical(layers.textLayer.position) === 0
            ? (layers.horizontal(layers.textLayer.position) === 0 ? Item.TopLeft : layers.horizontal(layers.textLayer.position) === 2 ? Item.TopRight : Item.Top)
            : layers.vertical(layers.textLayer.position) === 2
            ? (layers.horizontal(layers.textLayer.position) === 0 ? Item.BottomLeft : layers.horizontal(layers.textLayer.position) === 2 ? Item.BottomRight : Item.Bottom)
            : (layers.horizontal(layers.textLayer.position) === 0 ? Item.Left : layers.horizontal(layers.textLayer.position) === 2 ? Item.Right : Item.Center)
        wrapMode: Text.Wrap
        horizontalAlignment: layers.horizontal(layers.textLayer.position) === 0 ? Text.AlignLeft : layers.horizontal(layers.textLayer.position) === 2 ? Text.AlignRight : Text.AlignHCenter
        verticalAlignment: layers.vertical(layers.textLayer.position) === 0 ? Text.AlignTop : layers.vertical(layers.textLayer.position) === 2 ? Text.AlignBottom : Text.AlignVCenter
        style: layers.textStyle.plate ? Text.Normal : Text.Outline
        styleColor: "#111111"
    }
}
