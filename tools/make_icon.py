"""Draw the WorldClock app icon and write icon.ico (16-256 px, PNG entries) + docs/images/icon.png.

    python tools/make_icon.py

A dark glass disc in the pill's colours: soft rim light, two white hands at 10:10
and a small warm sun dot at 12. Each size is painted natively so 16 px stays crisp.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen,
                           QRadialGradient)
import math

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw(s: int) -> QImage:
    img = QImage(s, s, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(s / 2, s / 2)
    r = s * 0.46
    small = s <= 24

    # body: dark disc, slightly lifted at the top like the pill
    g = QRadialGradient(QPointF(s / 2, s * 0.30), r * 1.4)
    g.setColorAt(0.0, QColor(46, 46, 54))
    g.setColorAt(1.0, QColor(14, 14, 17))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(c, r, r)

    # rim: brighter at top-left (light from the top-left, as in glass.py)
    rim = QLinearGradient(QPointF(0, 0), QPointF(s, s))
    rim.setColorAt(0.0, QColor(255, 255, 255, 200))
    rim.setColorAt(0.5, QColor(255, 255, 255, 50))
    rim.setColorAt(1.0, QColor(255, 255, 255, 110))
    rw = max(1.0, s * 0.035)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(rim, rw))
    p.drawEllipse(c, r - rw / 2, r - rw / 2)

    # hour ticks at 3 / 6 / 9 (12 is the sun dot)
    if not small:
        tp = QPen(QColor(255, 255, 255, 90), max(1.0, s * 0.03))
        tp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(tp)
        for a in (90, 180, 270):
            d = QPointF(math.sin(math.radians(a)), -math.cos(math.radians(a)))
            p.drawLine(c + d * r * 0.70, c + d * r * 0.80)

    # sun dot at 12
    sr = max(1.2, s * 0.065)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 196, 82))
    p.drawEllipse(c + QPointF(0, -r * 0.72), sr, sr)

    # hands at 10:10
    def hand(deg, length, width):
        pen = QPen(QColor(245, 245, 245), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        d = QPointF(math.sin(math.radians(deg)), -math.cos(math.radians(deg)))
        p.drawLine(c, c + d * length)

    hand(305, r * 0.42, max(1.4, s * 0.085))     # hour, a bit past 10
    hand(60, r * 0.60, max(1.2, s * 0.065))      # minute at 10
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(245, 245, 245))
    p.drawEllipse(c, max(1.0, s * 0.05), max(1.0, s * 0.05))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    blobs = [(s, png_bytes(draw(s))) for s in SIZES]
    head = struct.pack("<HHH", 0, 1, len(blobs))
    off = 6 + 16 * len(blobs)
    dirs, data = b"", b""
    for s, b in blobs:
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), off + len(data))
        data += b
    (ROOT / "icon.ico").write_bytes(head + dirs + data)
    draw(256).save(str(ROOT / "docs" / "images" / "icon.png"))
    print("wrote icon.ico, docs/images/icon.png")


if __name__ == "__main__":
    main()
