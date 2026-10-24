"""Generate the small UI icons used by the Telegram-style export CSS.

Run with:  python scripts/generate_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "eitaa_export" / "assets" / "images"
SCALE = 4


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw, float]:
    image = Image.new("RGBA", (size * SCALE, size * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), size * SCALE / 24.0


def _line(draw: ImageDraw.ImageDraw, points, scale: float, color, width: float = 2.0) -> None:
    draw.line([(x * scale, y * scale) for x, y in points], fill=color, width=round(width * scale), joint="curve")


def _rounded(draw: ImageDraw.ImageDraw, box, scale: float, color, width: float = 2.0, radius: float = 3.0) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(
        (x1 * scale, y1 * scale, x2 * scale, y2 * scale),
        radius=radius * scale,
        outline=color,
        width=round(width * scale),
    )


def _icon(name: str, color: tuple[int, int, int, int]) -> Image.Image:
    size = 24
    image, draw, scale = _canvas(size)
    white = color

    if name in {"back"}:
        _line(draw, [(15, 4), (7, 12), (15, 20)], scale, white, 2.4)
    elif name == "media_photo":
        _rounded(draw, (3, 5, 21, 19), scale, white, 2.0, 2.5)
        draw.ellipse(
            (7.2 * scale, 8.2 * scale, 10.4 * scale, 11.4 * scale),
            fill=white,
        )
        _line(draw, [(4, 18), (10, 12), (14, 16), (17, 13), (20, 16)], scale, white, 1.8)
    elif name == "media_video":
        _rounded(draw, (3, 6, 21, 18), scale, white, 2.0, 3.0)
        draw.polygon(
            [(10 * scale, 9 * scale), (10 * scale, 15 * scale), (16 * scale, 12 * scale)],
            fill=white,
        )
    elif name == "media_voice":
        _rounded(draw, (9.5, 3, 14.5, 14), scale, white, 2.0, 2.5)
        draw.arc((6 * scale, 8 * scale, 18 * scale, 17 * scale), 20, 160, fill=white, width=round(1.8 * scale))
        _line(draw, [(12, 17), (12, 21)], scale, white, 1.8)
        _line(draw, [(9, 21), (15, 21)], scale, white, 1.8)
    elif name == "media_music":
        draw.ellipse((6 * scale, 15 * scale, 10.5 * scale, 19.5 * scale), fill=white)
        draw.ellipse((14.5 * scale, 13 * scale, 19 * scale, 17.5 * scale), fill=white)
        _line(draw, [(10, 17.5), (10, 6), (19, 4.5), (19, 15.5)], scale, white, 1.8)
    elif name in {"media_file", "section_other"}:
        _line(draw, [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)], scale, white, 1.9)
        _line(draw, [(14, 3), (14, 8), (19, 8)], scale, white, 1.6)
        _line(draw, [(9, 12), (16, 12)], scale, white, 1.4)
        _line(draw, [(9, 15.5), (16, 15.5)], scale, white, 1.4)
    elif name in {"media_contact", "section_contacts"}:
        draw.ellipse((9 * scale, 3.5 * scale, 15 * scale, 9.5 * scale), outline=white, width=round(1.9 * scale))
        draw.arc((5 * scale, 11 * scale, 19 * scale, 23 * scale), 180, 360, fill=white, width=round(1.9 * scale))
    elif name in {"media_location", "section_frequent"}:
        if name.endswith("location"):
            draw.ellipse((8 * scale, 3 * scale, 16 * scale, 11 * scale), outline=white, width=round(1.9 * scale))
            _line(draw, [(8.5, 9.5), (12, 21), (15.5, 9.5)], scale, white, 1.9)
            draw.ellipse((11 * scale, 6 * scale, 13 * scale, 8 * scale), fill=white)
        else:
            points = []
            import math

            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                radius = 9.5 if index % 2 == 0 else 4.2
                points.append((12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
            draw.polygon([(x * scale, y * scale) for x, y in points], fill=white)
    elif name == "media_game":
        _rounded(draw, (3, 7, 21, 18), scale, white, 2.0, 4.0)
        _line(draw, [(7, 11), (7, 15)], scale, white, 1.7)
        _line(draw, [(5, 13), (9, 13)], scale, white, 1.7)
        draw.ellipse((15 * scale, 11 * scale, 17 * scale, 13 * scale), fill=white)
        draw.ellipse((18 * scale, 13.5 * scale, 20 * scale, 15.5 * scale), fill=white)
    elif name in {"media_call", "section_calls"}:
        _line(draw, [(5, 4), (9, 4), (11, 9), (8.5, 11), (13, 15.5), (15, 13), (20, 15), (20, 19), (17, 21), (7, 19), (4, 8), (5, 4)], scale, white, 1.8)
    elif name in {"media_shop", "section_sessions"}:
        if name == "media_shop":
            _rounded(draw, (4, 8, 20, 21), scale, white, 1.9, 2.0)
            draw.arc((8 * scale, 3 * scale, 16 * scale, 13 * scale), 180, 360, fill=white, width=round(1.8 * scale))
        else:
            _rounded(draw, (3, 6, 21, 17), scale, white, 1.9, 2.0)
            _line(draw, [(2, 20), (22, 20)], scale, white, 1.8)
            _line(draw, [(8, 10), (16, 10)], scale, white, 1.4)
    elif name in {"section_chats"}:
        _rounded(draw, (3, 4, 21, 17), scale, white, 1.9, 3.0)
        _line(draw, [(8, 17), (7, 21), (12, 17)], scale, white, 1.9)
        _line(draw, [(7, 9), (17, 9)], scale, white, 1.4)
        _line(draw, [(7, 12.5), (14, 12.5)], scale, white, 1.4)
    elif name in {"section_photos"}:
        _rounded(draw, (3, 5, 21, 19), scale, white, 2.0, 2.5)
        draw.ellipse((7.5 * scale, 8 * scale, 10.5 * scale, 11 * scale), fill=white)
        _line(draw, [(4, 18), (10, 12), (14, 16), (17, 13), (20, 16)], scale, white, 1.8)
    elif name == "section_music":
        draw.ellipse((6 * scale, 15 * scale, 10.5 * scale, 19.5 * scale), fill=white)
        draw.ellipse((14.5 * scale, 13 * scale, 19 * scale, 17.5 * scale), fill=white)
        _line(draw, [(10, 17.5), (10, 6), (19, 4.5), (19, 15.5)], scale, white, 1.8)
    elif name == "section_stories":
        draw.arc((4 * scale, 4 * scale, 20 * scale, 20 * scale), 0, 360, fill=white, width=round(1.9 * scale))
        draw.ellipse((10 * scale, 10 * scale, 14 * scale, 14 * scale), fill=white)
    elif name == "section_web":
        draw.arc((3 * scale, 3 * scale, 21 * scale, 21 * scale), 0, 360, fill=white, width=round(1.8 * scale))
        _line(draw, [(3, 12), (21, 12)], scale, white, 1.5)
        draw.arc((8 * scale, 3 * scale, 16 * scale, 21 * scale), 0, 360, fill=white, width=round(1.5 * scale))
    else:
        for x, y in ((7, 7), (12, 7), (17, 7), (7, 12), (12, 12), (17, 12), (7, 17), (12, 17), (17, 17)):
            draw.ellipse(
                ((x - 1) * scale, (y - 1) * scale, (x + 1) * scale, (y + 1) * scale),
                fill=white,
            )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    media = [
        "media_call",
        "media_contact",
        "media_file",
        "media_game",
        "media_location",
        "media_music",
        "media_photo",
        "media_shop",
        "media_video",
        "media_voice",
    ]
    sections = [
        "section_calls",
        "section_chats",
        "section_contacts",
        "section_frequent",
        "section_music",
        "section_other",
        "section_photos",
        "section_sessions",
        "section_stories",
        "section_web",
    ]
    for name in media:
        image = _icon(name, (255, 255, 255, 255))
        image.save(OUT / f"{name}.png")
        image.resize((48, 48), Image.Resampling.LANCZOS).save(OUT / f"{name}@2x.png")
    for name in sections:
        image = _icon(name, (112, 119, 123, 255))
        image.save(OUT / f"{name}.png")
        image.resize((48, 48), Image.Resampling.LANCZOS).save(OUT / f"{name}@2x.png")
    back = _icon("back", (22, 138, 205, 255))
    back.save(OUT / "back.png")
    back.resize((48, 48), Image.Resampling.LANCZOS).save(OUT / "back@2x.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
