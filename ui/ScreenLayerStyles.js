.pragma library

var text = [
    {key: "classic", label: "经典描边", description: "直接叠在画面上", position: "top-center", fontSize: 40, color: "#FFFFFF"},
    {key: "champagne", label: "银弧序幕", description: "居中大字 · 冷色弧光", position: "top-center", fontSize: 70, color: "#F8FAFF",
     art: "ScreenSilverArc.svg", artWidth: .82, fontWeight: 500},
    {key: "midnight", label: "赤月舞台", description: "左侧主标题 · 右侧红月", position: "top-left", fontSize: 76, color: "#FFF4F1",
     art: "ScreenRedEclipse.svg", artWidth: .92, fontWeight: 600},
    {key: "record", label: "唱片切片", description: "左对齐 · 黑白封面结构", position: "top-left", fontSize: 56, color: "#241722",
     plate: "#F4F0E9", accent: "#211E22"}
];
var logo = [
    {key: "plain", label: "原图角标", position: "top-left", size: 14},
    {key: "matte", label: "银线署名", position: "top-left", size: 14,
     plate: "#B2171820", accent: "#C7D0DE"},
    {key: "foil", label: "舞台侧光", position: "top-left", size: 12,
     plate: "#A6181012", accent: "#F87557"}
];

function find(rows, key) {
    return rows.find(function(row) { return row.key === key; }) || rows[0];
}
