pragma Singleton
import QtQuick

// 深灰工作面与黑色监看区分层；紫色标记可操作的主项。
QtObject {
    // 表面层级（暗 -> 亮）
    readonly property color sunken: "#101115"
    readonly property color canvas: "#191a20"
    readonly property color surface: "#202127"
    readonly property color elevated: "#2c2e36"
    readonly property color glass: "#25262e"
    readonly property color hairline: "#1cffffff"
    readonly property color hairlineStrong: "#33ffffff"
    readonly property color wash: reduceTransparency ? elevated : "#0dffffff"
    readonly property color washHover: reduceTransparency ? strokeStrong : "#1cffffff"
    readonly property color stroke: "#30323a"
    readonly property color strokeStrong: "#464955"
    readonly property color strokeHover: "#636672"
    // 带紫调的卡片（导入卡、提示卡）
    readonly property color cardTint: "#292536"
    readonly property color cardTintLine: "#4e435f"
    // 文字
    readonly property color text: "#f5f5f7"
    readonly property color secondary: "#bdbdc4"
    readonly property color muted: "#a0a2ae"
    readonly property color accent: "#bb86ff"
    readonly property color accentSoft: "#41344f"
    readonly property color accentLine: "#7d5bc2"
    readonly property color accentText: "#ffffff"
    readonly property color accentHover: "#e0b8ff"
    readonly property color accentPressed: "#b57df5"
    readonly property color accentSoftHover: "#503f66"
    readonly property color accentSolid: "#8044c5"
    // 焦点与开关
    readonly property color focus: "#5b9dff"
    readonly property color toggle: "#a873f5"
    // 状态：文字色 + 浅底 + 描边
    readonly property color green: "#8fdcb4"
    readonly property color greenSoft: "#0e1d17"
    readonly property color greenLine: "#2c5a46"
    readonly property color amber: "#f2c27f"
    readonly property color amberSoft: "#221a10"
    readonly property color amberLine: "#6b5433"
    readonly property color red: "#ff9aa6"
    readonly property color redSoft: "#25111a"
    readonly property color redLine: "#8c4458"
    readonly property color blue: "#7cb6ff"
    readonly property color blueSoft: "#0e1828"
    readonly property color blueLine: "#35618f"
    // 控件
    readonly property color disabledSurface: "#121214"
    readonly property color hoverSurface: "#1f1f24"
    readonly property color pressedSurface: "#29292f"
    // 尺寸
    readonly property int radiusControl: 6
    readonly property int radiusCard: 8
    readonly property int radiusSection: 10
    readonly property int pagePadding: 20
    readonly property int navHeight: 48
    // 字号：包房暗光下比网页模板略大
    readonly property int fontPageTitle: 20
    readonly property int fontSection: 14
    readonly property int fontBody: 13
    readonly property int fontCaption: 11
    readonly property real titleSpacing: -0.4
    readonly property string numberFont: "Menlo"
    property bool reduceMotion: false
    property bool reduceTransparency: false

    function toneInk(tone) { return tone === "green" ? green : tone === "amber" ? amber : tone === "red" ? red : tone === "blue" ? blue : tone === "accent" ? accent : secondary; }
    function toneSoft(tone) { return tone === "green" ? greenSoft : tone === "amber" ? amberSoft : tone === "red" ? redSoft : tone === "blue" ? blueSoft : tone === "accent" ? accentSoft : wash; }
    function toneLine(tone) { return tone === "green" ? greenLine : tone === "amber" ? amberLine : tone === "red" ? redLine : tone === "blue" ? blueLine : tone === "accent" ? accentLine : hairline; }
}
