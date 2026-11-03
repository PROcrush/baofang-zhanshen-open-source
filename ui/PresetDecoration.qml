import QtQuick

// Original, caption-local marks. These never add an opaque full-screen layer
// to the Serato host, and only repaint when the caption geometry/style changes.
Item {
    id: decoration
    objectName: "presetDecoration"
    property string mode: "none"
    property color accent: "#F0C17A"
    property color secondary: "#16F4D4"
    property color background: "#08090C"
    property color foreground: "#F5EEEA"
    property real scaleFactor: 1
    property real padding: 18 * scaleFactor
    readonly property string label: mode === "hrNightRec" ? "● REC" : mode === "transit" ? "ON AIR" : mode === "caution" ? "LIVE" : mode === "hud" ? "LYRIC //" : mode === "mint" ? ">_" : mode === "blueprint" ? "01 / LIVE" : mode === "specimen" ? "Aa" : mode === "newsprint" ? "LIVE EDITION" : mode === "hrCurse" ? "✕" : ""

    function repaint() { marks.requestPaint() }
    onModeChanged: repaint()
    onAccentChanged: repaint()
    onSecondaryChanged: repaint()
    onBackgroundChanged: repaint()
    onForegroundChanged: repaint()
    onScaleFactorChanged: repaint()
    onPaddingChanged: repaint()
    onVisibleChanged: if (visible) repaint()

    Canvas {
        id: marks
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c = getContext("2d");
            c.clearRect(0, 0, width, height);
            if (decoration.mode === "none") return;
            var s = decoration.scaleFactor, p = decoration.padding;
            var a = decoration.accent, b = decoration.secondary, bg = decoration.background;
            var mode = decoration.mode;
            var x = 2 * s, y = 2 * s, w = Math.max(0, width - 4 * s), h = Math.max(0, height - 4 * s);
            function line(x1, y1, x2, y2, color, thickness, opacity) {
                c.globalAlpha = opacity === undefined ? 1 : opacity;
                c.strokeStyle = color; c.lineWidth = thickness * s;
                c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
                c.globalAlpha = 1;
            }
            function plate(color, opacity) {
                c.globalAlpha = opacity; c.fillStyle = color;
                c.fillRect(x, y, w, h); c.globalAlpha = 1;
            }
            function frame(color, opacity, dash) {
                c.globalAlpha = opacity; c.strokeStyle = color; c.lineWidth = s;
                c.setLineDash(dash || []); c.strokeRect(x + s, y + s, w - 2 * s, h - 2 * s);
                c.setLineDash([]); c.globalAlpha = 1;
            }
            function corners(color, length) {
                var l = length * s;
                line(x, y + l, x, y, color, 1.5, .7); line(x, y, x + l, y, color, 1.5, .7);
                line(x + w - l, y, x + w, y, color, 1.5, .7); line(x + w, y, x + w, y + l, color, 1.5, .7);
                line(x, y + h - l, x, y + h, color, 1.5, .7); line(x, y + h, x + l, y + h, color, 1.5, .7);
                line(x + w - l, y + h, x + w, y + h, color, 1.5, .7); line(x + w, y + h, x + w, y + h - l, color, 1.5, .7);
            }
            function gradientBar(yPosition, thickness, opacity) {
                var g = c.createLinearGradient(x + p, 0, x + w - p, 0);
                g.addColorStop(0, a); g.addColorStop(1, b);
                c.globalAlpha = opacity; c.fillStyle = g;
                c.fillRect(x + p, yPosition, Math.max(0, w - p * 2), thickness * s);
                c.globalAlpha = 1;
            }
            if (mode === "paper" || mode === "specimen" || mode === "newsprint" || mode === "kraft" || mode === "forest" || mode === "sumi") {
                plate(bg, .90);
                if (mode === "paper") {
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, a, 2, .55);
                } else if (mode === "specimen") {
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, a, .7, .5);
                    for (var i = 0; i < 9; i++) line(x + p + (w - 2 * p) * i / 8, y + h - 10 * s, x + p + (w - 2 * p) * i / 8, y + h - 3 * s, a, .6, .4);
                } else if (mode === "newsprint") {
                    line(x + p, y + 8 * s, x + w - p, y + 8 * s, a, 1, .5);
                    line(x + p, y + h - 7 * s, x + w - p, y + h - 7 * s, a, 2, .55);
                    line(x + p, y + h - 11 * s, x + w - p, y + h - 11 * s, a, .6, .45);
                } else if (mode === "kraft") {
                    frame(a, .40, [4 * s, 4 * s]);
                    for (var k = 0; k < 16; k++) {
                        var tx = x + ((k * 61) % Math.max(1, w)), ty = y + ((k * 19) % Math.max(1, h));
                        line(tx, ty, Math.min(x + w, tx + 2 * s), ty + s, a, .7, .12);
                    }
                } else if (mode === "forest") {
                    line(x + 7 * s, y + p, x + 7 * s, y + h - p, a, 3, .6);
                    line(x + 11 * s, y + p, x + 11 * s, y + h - p, b, .6, .4);
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, a, .7, .3);
                } else {
                    line(x + p, y + h - 8 * s, x + w - p, y + h - 10 * s, a, 3, .5);
                    c.fillStyle = b; c.globalAlpha = .65; c.fillRect(x + w - 12 * s, y + 6 * s, 6 * s, 6 * s); c.globalAlpha = 1;
                }
            } else if (mode === "hud" || mode === "mint" || mode === "blueprint") {
                if (mode === "hud") { corners(a, 15); line(x + w * .62, y + h, x + w - 18 * s, y + h, b, 1, .4); }
                if (mode === "mint") { line(x + 4 * s, y + p, x + 4 * s, y + h - p, a, 2, .55); line(x + p, y + h - 4 * s, x + p + 13 * s, y + h - 4 * s, b, 3, .7); }
                if (mode === "blueprint") {
                    frame(a, .55); corners(b, 5);
                    for (var j = 1; j < 5; j++) line(x + w * j / 5, y + h - 5 * s, x + w * j / 5, y + h, a, .7, .5);
                }
            } else if (mode === "caution" || mode === "transit") {
                if (mode === "caution") {
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, a, 2, .55);
                    for (var n = 0; n < 4; n++) line(x + 3 * s + n * 5 * s, y + h - p, x + 8 * s + n * 5 * s, y + h - p - 7 * s, a, 2, .7);
                } else {
                    line(x + 5 * s, y + p, x + 5 * s, y + h - p, a, 4, .7);
                    line(x + p, y + h - 5 * s, x + w - p, y + h - 5 * s, b, 1, .4);
                }
            } else if (mode === "hrRuin" || mode === "hrNightRec" || mode === "hrCurse") {
                if (mode === "hrRuin") {
                    line(x + p, y + h - 4 * s, x + w * .43, y + h - 7 * s, a, 2, .55);
                    line(x + w * .48, y + h - 5 * s, x + w - p, y + h - 3 * s, b, 1, .4);
                    line(x + 5 * s, y + p, x + 8 * s, y + h - p, b, 1, .4);
                } else if (mode === "hrNightRec") {
                    corners(a, 11);
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, b, .6, .25);
                } else {
                    frame(a, .3, [9 * s, 4 * s]);
                    line(x + p, y + h - 6 * s, x + w - p, y + h - 9 * s, a, 1, .6);
                }
            } else if (mode === "noir" || mode === "magenta" || mode === "mono") {
                line(x + p, y + h - 5 * s, x + w * .55, y + h - 5 * s, a, mode === "mono" ? 1 : 2, .5);
                line(x + w * .45, y + h - 3 * s, x + w - p, y + h - 3 * s, b, mode === "magenta" ? 3 : 1, .5);
            } else if (mode === "crimson") {
                line(x + 5 * s, y + p, x + 5 * s, y + h - p, a, 3, .75);
                line(x + p, y + h - 6 * s, x + w - p, y + h - 6 * s, a, 1, .4);
                c.fillStyle = b; c.globalAlpha = .6; c.fillRect(x + w - 9 * s, y + 5 * s, 4 * s, 4 * s); c.globalAlpha = 1;
            } else {
                // Distinct, static marks for each expressive gradient family.
                if (mode === "rouge") { gradientBar(y + h - 5 * s, 3, .65); }
                else if (mode === "sakura") {
                    for (var petal = 0; petal < 3; petal++) { c.fillStyle = petal === 1 ? b : a; c.globalAlpha = .35; c.beginPath(); c.arc(x + w - 8 * s, y + (9 + petal * 9) * s, 3 * s, 0, Math.PI * 2); c.fill(); } c.globalAlpha = 1;
                    gradientBar(y + h - 4 * s, 1, .45);
                } else if (mode === "ocean") {
                    for (var wave = 0; wave < 2; wave++) { c.strokeStyle = wave === 0 ? a : b; c.globalAlpha = .4; c.lineWidth = s; c.beginPath(); c.moveTo(x + p, y + h - (6 + wave * 4) * s); c.bezierCurveTo(x + w * .3, y + h - (14 + wave * 4) * s, x + w * .7, y + h + (2 - wave * 4) * s, x + w - p, y + h - (6 + wave * 4) * s); c.stroke(); } c.globalAlpha = 1;
                } else if (mode === "sunset") { gradientBar(y + h - 6 * s, 2, .6); line(x + w * .4, y + h - 10 * s, x + w * .6, y + h - 10 * s, b, .6, .4); }
                else if (mode === "vapor") { corners(a, 9); gradientBar(y + h - 5 * s, 1, .5); line(x + w * .75, y + h - 9 * s, x + w - p, y + h - 9 * s, b, 1, .6); }
                else if (mode === "synth80") { gradientBar(y + h - 5 * s, 2, .6); gradientBar(y + h - 10 * s, .6, .3); corners(b, 5); }
                else if (mode === "candy") { c.strokeStyle = a; c.lineWidth = 2 * s; c.globalAlpha = .4; c.beginPath(); c.arc(x + 7 * s, y + h * .5, 4 * s, 0, Math.PI * 2); c.stroke(); c.strokeStyle = b; c.beginPath(); c.arc(x + w - 7 * s, y + h * .5, 4 * s, 0, Math.PI * 2); c.stroke(); c.globalAlpha = 1; gradientBar(y + h - 5 * s, 1, .5); }
                else if (mode === "acid") { line(x + p, y + h - 5 * s, x + w * .45, y + h - 5 * s, a, 3, .6); line(x + w * .5, y + h - 5 * s, x + w - p, y + h - 10 * s, b, 2, .55); }
                else if (mode === "gold") { frame(a, .28); corners(b, 4); gradientBar(y + h - 6 * s, 1, .6); }
            }
        }
    }
    Text {
        visible: decoration.label !== ""
        x: 18 * decoration.scaleFactor
        y: -4 * decoration.scaleFactor
        text: decoration.label
        color: decoration.accent
        font.family: "Menlo"
        font.pixelSize: Math.max(1, 9 * decoration.scaleFactor)
        font.letterSpacing: decoration.scaleFactor
        opacity: .65
    }
}
