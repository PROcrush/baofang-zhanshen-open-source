.pragma library

var text = [
    {key: "classic", label: "经典描边", description: "直接叠在画面上", position: "top-center", fontSize: 40, color: "#FFFFFF"},
    {key: "champagne", label: "银弧序幕", description: "冬青黑体 · 左上留边", position: "top-left", fontSize: 76, color: "#F8FAFF",
     art: "ScreenSilverArc.svg", artWidth: .77, artLeft: .04, font: "hiragino", tracking: .055,
     left: .105, top: .20},
    {key: "midnight", label: "赤月舞台", description: "宋体重字 · 右侧落版", position: "top-right", fontSize: 88, color: "#FFF4F1",
     art: "ScreenRedEclipse.svg", artWidth: .96, mirrorArt: true, font: "songti", tracking: .025,
     left: .40, right: .09, top: .26},
    {key: "record", label: "唱片切片", description: "黑体 · 上方居中封面", position: "top-center", fontSize: 57, color: "#241722",
     plate: "#F4F0E9", accent: "#211E22", font: "heiti", top: .125}
];
var fonts = [
    {key: "auto", label: "模板字体"},
    {key: "pingfang", label: "苹方", family: "PingFang SC"},
    {key: "hiragino", label: "冬青黑体", family: "Hiragino Sans GB", face: "W3"},
    {key: "songti", label: "宋体重字", family: "Songti SC", face: "Black"},
    {key: "heiti", label: "黑体", family: "Heiti SC", face: "Medium"}
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
