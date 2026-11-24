"""Generate the application icon without third-party imaging libraries."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rounded_rect_alpha(x: float, y: float, size: float, radius: float, inset: float) -> float:
    left = inset
    top = inset
    right = size - inset
    bottom = size - inset
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    dx = x - cx
    dy = y - cy
    distance = (dx * dx + dy * dy) ** 0.5
    return max(0.0, min(1.0, radius + 0.5 - distance))


def render_icon(size: int, scale: int = 4) -> bytes:
    big = size * scale
    pixels: list[tuple[int, int, int, int]] = []
    top_color = (151, 123, 255)
    bottom_color = (83, 58, 214)
    mint = (67, 215, 163)
    white = (255, 255, 255)
    for y in range(big):
        for x in range(big):
            # Background rounded square with a diagonal gradient.
            alpha = _rounded_rect_alpha(x + 0.5, y + 0.5, big, big * 0.22, big * 0.02)
            t = (x / big) * 0.35 + (y / big) * 0.65
            color = _mix(top_color, bottom_color, t)
            # A subtle light sweep.
            sweep = max(0.0, 1.0 - abs((x + y) / (big * 2) - 0.28) * 5)
            color = _mix(color, (190, 174, 255), sweep * 0.16)
            if alpha <= 0:
                pixels.append((0, 0, 0, 0))
                continue

            # Five waveform bars, rounded caps.
            bar_width = big * 0.068
            gap = big * 0.036
            center_x = big / 2
            center_y = big / 2
            heights = [0.27, 0.47, 0.66, 0.43, 0.29]
            total_width = len(heights) * bar_width + (len(heights) - 1) * gap
            start_x = center_x - total_width / 2
            wave_alpha = 0.0
            wave_index = 0
            for index, height_ratio in enumerate(heights):
                bx1 = start_x + index * (bar_width + gap)
                bx2 = bx1 + bar_width
                by1 = center_y - big * height_ratio / 2
                by2 = center_y + big * height_ratio / 2
                if bx1 - 0.5 <= x <= bx2 + 0.5 and by1 - 0.5 <= y <= by2 + 0.5:
                    radius = bar_width / 2
                    cx = min(max(x, bx1 + radius), bx2 - radius)
                    cy = min(max(y, by1 + radius), by2 - radius)
                    if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                        wave_alpha = 1.0
                        wave_index = index
            if wave_alpha:
                wave_color = mint if wave_index == 2 else white
                color = _mix(color, wave_color, 0.96)
            pixels.append((*color, round(alpha * 255)))

    # Box-filter downsample for smooth edges.
    output: list[tuple[int, int, int, int]] = []
    for y in range(size):
        for x in range(size):
            rs = gs = bs = alpha_sum = 0
            for yy in range(scale):
                for xx in range(scale):
                    source = pixels[(y * scale + yy) * big + (x * scale + xx)]
                    alpha = source[3]
                    rs += source[0] * alpha
                    gs += source[1] * alpha
                    bs += source[2] * alpha
                    alpha_sum += alpha
            if alpha_sum == 0:
                output.append((0, 0, 0, 0))
            else:
                output.append(
                    (
                        round(rs / alpha_sum),
                        round(gs / alpha_sum),
                        round(bs / alpha_sum),
                        round(alpha_sum / (scale * scale)),
                    )
                )
    return encode_png(size, size, output)


def encode_png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels[y * width + x])

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = []
    payload = b""
    for size, png in images:
        width_byte = 0 if size >= 256 else size
        height_byte = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                width_byte,
                height_byte,
                0,
                0,
                1,
                32,
                len(png),
                offset,
            )
        )
        offset += len(png)
        payload += png
    return header + b"".join(entries) + payload


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    png_256 = render_icon(256)
    (ASSETS / "icon.png").write_bytes(png_256)
    icon_images: list[tuple[int, bytes]] = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon_images.append((size, render_icon(size, scale=4 if size <= 64 else 3)))
    (ASSETS / "icon.ico").write_bytes(encode_ico(icon_images))
    print(f"Wrote {ASSETS / 'icon.png'} and {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
