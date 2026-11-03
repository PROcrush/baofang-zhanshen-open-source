import QtQuick

Item {
    id: layers
    property var stateData: ({})
    readonly property var config: stateData.screenLayers || ({})
    readonly property var textLayer: config.text || ({})
    readonly property var logoLayer: config.logo || ({})
    readonly property real scale: width / 1120
    readonly property real margin: Math.min(width, height) * .045
    function horizontal(position) {
        return position && position.indexOf("left") >= 0 ? 0 : position && position.indexOf("right") >= 0 ? 2 : 1;
    }
    function vertical(position) {
        return position && position.indexOf("top") >= 0 ? 0 : position && position.indexOf("bottom") >= 0 ? 2 : 1;
    }
    Image {
        id: logo
        objectName: "screenLogoImage"
        source: layers.stateData.logoImageUrl || ""
        visible: !!layers.logoLayer.visible && status === Image.Ready
        width: layers.width * (layers.logoLayer.size || 18) / 100
        height: Math.min(layers.height - 2 * layers.margin, width * sourceSize.height / Math.max(1, sourceSize.width))
        x: layers.horizontal(layers.logoLayer.position) === 0 ? layers.margin : layers.horizontal(layers.logoLayer.position) === 2 ? layers.width-width-layers.margin : (layers.width-width)/2
        y: layers.vertical(layers.logoLayer.position) === 0 ? layers.margin : layers.vertical(layers.logoLayer.position) === 2 ? layers.height-height-layers.margin : (layers.height-height)/2
        opacity: layers.logoLayer.opacity === undefined ? 1 : layers.logoLayer.opacity
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        mipmap: true
    }
    Text {
        objectName: "screenTextLayer"
        anchors.fill: parent
        anchors.margins: layers.margin
        visible: !!layers.textLayer.visible
        text: layers.textLayer.text || ""
        textFormat: Text.PlainText
        color: layers.textLayer.color || "#FFFFFF"
        opacity: layers.textLayer.opacity === undefined ? 1 : layers.textLayer.opacity
        font.family: "PingFang SC"
        font.bold: true
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
        style: Text.Outline
        styleColor: "#111111"
    }
}
