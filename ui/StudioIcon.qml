import QtQuick

Canvas {
    id: icon
    property string name: "import"
    property color ink: Theme.secondary
    implicitWidth: 20
    implicitHeight: 20
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d");
        c.clearRect(0, 0, width, height);
        c.save(); c.scale(width / 24, height / 24);
        c.strokeStyle = ink; c.fillStyle = ink;
        c.lineWidth = 1.65; c.lineCap = "round"; c.lineJoin = "round";
        function line(x, y, xx, yy) { c.beginPath(); c.moveTo(x, y); c.lineTo(xx, yy); c.stroke(); }
        function box(x, y, w, h, r) {
            c.beginPath(); c.moveTo(x+r,y); c.lineTo(x+w-r,y); c.quadraticCurveTo(x+w,y,x+w,y+r);
            c.lineTo(x+w,y+h-r); c.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
            c.lineTo(x+r,y+h); c.quadraticCurveTo(x,y+h,x,y+h-r); c.lineTo(x,y+r); c.quadraticCurveTo(x,y,x+r,y); c.stroke();
        }
        if (name === "import" || name === "export") {
            line(5,15,5,20); line(5,20,19,20); line(19,20,19,15); line(12,3,12,15);
            var y = name === "import" ? 15 : 3; var yy = name === "import" ? 10 : 8;
            line(8,yy,12,y); line(16,yy,12,y);
        } else if (name === "project") { box(3,6,18,14,2); line(3,6,3,4); line(3,4,10,4); line(10,4,12,6); }
        else if (name === "save") { box(4,3,16,18,2); box(8,3,8,6,1); box(7,14,10,7,1); }
        else if (name === "undo" || name === "redo") {
            if (name === "redo") { c.translate(24,0); c.scale(-1,1); }
            line(4,10,9,5); line(4,10,9,15); c.beginPath(); c.moveTo(4,10); c.lineTo(14,10); c.bezierCurveTo(21,10,21,19,14,19); c.stroke();
        } else if (name === "audio") { line(10,5,10,17); line(10,5,20,3); line(20,3,20,15); line(10,9,20,7); c.beginPath(); c.ellipse(7,18,3,2,0,0,2*Math.PI); c.stroke(); c.beginPath(); c.ellipse(17,16,3,2,0,0,2*Math.PI); c.stroke(); }
        else if (name === "lyrics") { box(3,4,18,16,3); line(7,9,17,9); line(7,13,17,13); line(7,17,13,17); }
        else if (name === "background") { box(3,4,18,16,3); c.beginPath(); c.moveTo(4,17); c.lineTo(9,12); c.lineTo(13,16); c.lineTo(16,12); c.lineTo(20,17); c.stroke(); c.beginPath(); c.arc(16,8,1.5,0,2*Math.PI); c.stroke(); }
        else if (name === "tools") { line(4,6,20,6); line(4,12,20,12); line(4,18,20,18); box(7,4,3,4,1); box(15,10,3,4,1); box(9,16,3,4,1); }
        else if (name === "play") { c.beginPath(); c.moveTo(8,5); c.lineTo(19,12); c.lineTo(8,19); c.closePath(); c.fill(); }
        else if (name === "pause") { box(6,5,3,14,0.5); box(15,5,3,14,0.5); }
        else if (name === "previous" || name === "next") { if(name === "next") { c.translate(24,0); c.scale(-1,1); } line(5,5,5,19); c.beginPath(); c.moveTo(18,5); c.lineTo(8,12); c.lineTo(18,19); c.closePath(); c.stroke(); }
        else if (name === "zoomIn" || name === "zoomOut") { c.beginPath(); c.arc(10,10,6,0,2*Math.PI); c.stroke(); line(15,15,21,21); line(7,10,13,10); if(name === "zoomIn") line(10,7,10,13); }
        else if (name === "correct") { line(4,6,12,6); line(4,11,10,11); line(4,16,8,16); c.beginPath(); c.moveTo(13,15); c.lineTo(16,18); c.lineTo(22,11); c.stroke(); }
        else if (name === "film") { box(3,4,18,16,2); line(7,4,7,20); line(17,4,17,20); line(3,8,7,8); line(3,12,7,12); line(3,16,7,16); line(17,8,21,8); line(17,12,21,12); line(17,16,21,16); }
        else if (name === "chevron") { line(8,10,12,14); line(12,14,16,10); }
        else if (name === "close") { line(6,6,18,18); line(18,6,6,18); }
        else if (name === "check") { line(5,12,10,17); line(10,17,20,7); }
        c.restore();
    }
}
