.pragma library

function cardState(entry, thumbnail) {
    var appearance = Object.assign({}, entry.previewStyle)
    appearance.fontSize = thumbnail ? 190 : 72
    appearance.verticalPosition = 34
    appearance.horizontalMargin = 8
    appearance.lineMode = "single"
    appearance.outputMode = "standard"
    appearance.dynamicBackground = true
    return {selectedSource: "A", effectiveSource: "A", stale: false, outputBlocked: false,
        inputMode: "style-preview", scene: "amber", deckA: {cacheState: "ready"},
        style: appearance, lyric: {visible: true, text: thumbnail ? "合唱" : "让旋律照亮此刻", nextText: "一起唱出心中的歌",
            quality: "sentence", progress: 0.48}}
}
