"""Render the editable SVG to runtime PNG and native packaging icons."""
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = ROOT / "src/sync_server/assets"
    target = ROOT / "packaging/icons"
    target.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(str(assets / "app-icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid icon SVG")
    images = {}
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        picture = QImage(size, size, QImage.Format.Format_ARGB32)
        picture.fill(0)
        painter = QPainter(picture)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not picture.save(buffer, "PNG"):
            raise RuntimeError("PNG encoding failed")
        images[size] = bytes(data)
    (assets / "app-icon.png").write_bytes(images[512])
    sizes = (16, 24, 32, 48, 64, 128, 256)
    offset = 6 + 16 * len(sizes)
    entries = []
    for size in sizes:
        data = images[size]
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (target / "app.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(images[s] for s in sizes))
    chunks = []
    for kind, size in ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        data = images[size]
        chunks.append(kind + struct.pack(">I", len(data) + 8) + data)
    payload = b"".join(chunks)
    (target / "app.icns").write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    print(f"Generated PNG, ICO and ICNS from {assets / 'app-icon.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
