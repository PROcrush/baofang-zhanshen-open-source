import QtQuick

Canvas {
    id: icon
    property string name: "stage"
    property color ink: Theme.secondary
    implicitWidth: 22
    implicitHeight: 22
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d");
        c.clearRect(0, 0, width, height);
        c.save(); c.scale(width / 24, height / 24);
        c.strokeStyle = ink; c.fillStyle = ink;
        c.lineWidth = 1.7; c.lineCap = "round"; c.lineJoin = "round";
        function line(x1,y1,x2,y2) { c.beginPath(); c.moveTo(x1,y1); c.lineTo(x2,y2); c.stroke(); }
        function box(x,y,w,h,r) {
            c.beginPath(); c.moveTo(x+r,y); c.lineTo(x+w-r,y); c.quadraticCurveTo(x+w,y,x+w,y+r);
            c.lineTo(x+w,y+h-r); c.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
            c.lineTo(x+r,y+h); c.quadraticCurveTo(x,y+h,x,y+h-r); c.lineTo(x,y+r); c.quadraticCurveTo(x,y,x+r,y); c.stroke();
        }
        if (name === "stage") { box(3,4,18,13,3); line(8,21,16,21); line(12,17,12,21); line(7,10,7,12); line(11,8,11,14); line(15,9,15,13); line(18,10,18,12); }
        else if (name === "library") { box(3,4,5,16,1); box(10,4,5,16,1); line(18,5,21,19); line(5,8,6,8); line(12,8,13,8); }
        else if (name === "prepare") { line(4,8,4,16); line(8,5,8,19); line(12,3,12,21); line(16,6,16,18); line(20,9,20,15); }
        else if (name === "style") { box(3,4,18,16,4); line(7,15,10,8); line(10,8,13,15); line(8,12,12,12); line(16,8,17,8); line(16,12,17,12); line(16,16,17,16); }
        else if (name === "diagnostics") { line(4,6,20,6); line(4,12,20,12); line(4,18,20,18); box(8,4,3,4,1); box(15,10,3,4,1); box(6,16,3,4,1); }
        else if (name === "studio") { box(3,3,18,18,3); line(3,14,21,14); line(8,14,8,21); line(7,7,17,7); line(7,10,13,10); }
        else if (name === "arrow") { line(5,12,19,12); line(14,7,19,12); line(14,17,19,12); }
        c.restore();
    }
}
