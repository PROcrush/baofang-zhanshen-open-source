pragma Singleton
import QtQuick

// 2026-10-29 v4 参考 Framer 模板 Zenox：纯黑底、近黑卡片加一条极细白边、胶囊按钮，
// 粉紫→蓝渐变只做点缀（主按钮描边、选中短横条、栏标题短横条），大面积不铺色。
// 包房暗光：文字保持高对比，焦点用渐变里的蓝。颜色 #AARRGGBB 为半透明白。
QtObject {
    // 表面层级（暗 -> 亮）
    readonly property color sunken: "#000000"
    readonly property color canvas: "#060607"
    readonly property color surface: "#101012"
    readonly property color elevated: "#1a1a1e"
    readonly property color glass: "#0b0b0e"
    readonly property color hairline: "#1cffffff"
    readonly property color hairlineStrong: "#33ffffff"
    readonly property color wash: "#0dffffff"
    readonly property color washHover: "#1cffffff"
    readonly property color stroke: "#202024"
    readonly property color strokeStrong: "#34343a"
    readonly property color strokeHover: "#5c5c66"
    // 带紫调的卡片（导入卡、提示卡）
    readonly property color cardTint: "#120f18"
    readonly property color cardTintLine: "#3a2f52"
    // 文字
    readonly property color text: "#f5f5f7"
    readonly property color secondary: "#bdbdc4"
    readonly property color muted: "#85858e"
    // 主色：Zenox 渐变 粉紫 -> 蓝；单色场合用中间的浅紫
    readonly property color gradientStart: "#de96ff"
    readonly property color gradientEnd: "#417ed9"
    readonly property color glow: "#3dbd53fb"
    readonly property color accent: "#cf9cff"
    readonly property color accentSoft: "#1c1528"
    readonly property color accentLine: "#7d5bc2"
    readonly property color accentText: "#ffffff"
    readonly property color accentHover: "#e0b8ff"
    readonly property color accentPressed: "#b57df5"
    readonly property color accentSoftHover: "#271d38"
    readonly property color accentSolid: "#9d6cf0"
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
    readonly property int radiusControl: 10
    readonly property int radiusCard: 18
    readonly property int radiusSection: 24
    readonly property int pagePadding: 20
    readonly property int navHeight: 56
    // 字号：包房暗光下比网页模板略大
    readonly property int fontPageTitle: 20
    readonly property int fontSection: 14
    readonly property int fontBody: 13
    readonly property int fontCaption: 11
    readonly property real titleSpacing: -0.4
    // 数字与路径：Helvetica Neue 默认等宽数字，比 Menlo 现代
    readonly property string numberFont: "Helvetica Neue"
    property bool reduceMotion: false
    property bool reduceTransparency: false

    function toneInk(tone) { return tone === "green" ? green : tone === "amber" ? amber : tone === "red" ? red : tone === "blue" ? blue : tone === "accent" ? accent : secondary; }
    function toneSoft(tone) { return tone === "green" ? greenSoft : tone === "amber" ? amberSoft : tone === "red" ? redSoft : tone === "blue" ? blueSoft : tone === "accent" ? accentSoft : wash; }
    function toneLine(tone) { return tone === "green" ? greenLine : tone === "amber" ? amberLine : tone === "red" ? redLine : tone === "blue" ? blueLine : tone === "accent" ? accentLine : hairline; }
}
