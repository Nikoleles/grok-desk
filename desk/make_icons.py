"""Write the home-screen icons. Black tile, green sparkline."""

import struct
import zlib
from pathlib import Path


def _pixel(x, y, size):
    nx = x / (size - 1)
    ny = y / (size - 1)
    inset = 0.08
    if nx < inset or ny < inset or nx > 1 - inset or ny > 1 - inset:
        # soft rounded corner
        dx = min(nx - inset, 1 - inset - nx)
        dy = min(ny - inset, 1 - inset - ny)
        if dx < 0 or dy < 0:
            return (0, 0, 0, 0)
    # sparkline through the middle
    pts = [(0.22, 0.68), (0.40, 0.58), (0.55, 0.62), (0.78, 0.34)]
    span = pts[-1][0] - pts[0][0]
    t = (nx - pts[0][0]) / span
    if 0 <= t <= 1:
        i = min(2, int(t * 3))
        local = t * 3 - i
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        line_y = y0 + (y1 - y0) * local
        if abs(ny - line_y) < 0.035 and nx >= pts[0][0] - 0.01:
            return (0, 211, 90, 255)
    return (0, 0, 0, 255)


def write_png(path: Path, size: int):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(_pixel(x, y, size))
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    out = Path(__file__).resolve().parent / "web" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    write_png(out / "apple-touch-icon.png", 180)
    write_png(out / "icon-192.png", 192)
    write_png(out / "icon-512.png", 512)


if __name__ == "__main__":
    main()
