.pragma library

var text = [
    {key: "classic", label: "经典描边", position: "top-center", fontSize: 40, color: "#FFFFFF"},
    {key: "midnight", label: "午夜电波", position: "top-center", fontSize: 50, color: "#F3EBFF",
     plate: "#E6181424", accent: "#BD9EFA"},
    {key: "champagne", label: "香槟序章", position: "top-center", fontSize: 54, color: "#FFF0CE",
     plate: "#EA201812", accent: "#CDA66A", font: "Songti SC"},
    {key: "record", label: "唱片字卡", position: "top-left", fontSize: 49, color: "#241722",
     plate: "#FFF1E0", accent: "#8B3448"}
];
var logo = [
    {key: "plain", label: "原图角标", position: "top-left", size: 14},
    {key: "matte", label: "夜场铭牌", position: "top-left", size: 14,
     plate: "#E51B1727", accent: "#A992D8"},
    {key: "foil", label: "烫金印记", position: "top-left", size: 12,
     plate: "#F0231815", accent: "#D2A467"}
];

function find(rows, key) {
    return rows.find(function(row) { return row.key === key; }) || rows[0];
}
