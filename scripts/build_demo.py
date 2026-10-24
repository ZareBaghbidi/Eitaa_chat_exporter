"""Build a small offline demo export so the layout can be inspected without an Eitaa account.

Run with:  python scripts/build_demo.py
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from eitaa_export.models import ChatInfo, ForwardInfo, MediaInfo, Message
from eitaa_export.render import Renderer
from eitaa_export.text import render_text

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
ASSETS = ROOT / "src" / "eitaa_export" / "assets"
TZ = ZoneInfo("Asia/Tehran")
TS = int(datetime(2026, 8, 19, 5, 16, 4, tzinfo=TZ).timestamp())
TITLE = "مدرسه المپیاد کامپیوتر یزد"


def _offset(text: str, needle: str) -> int:
    return len(text[: text.index(needle)].encode("utf-16-le")) // 2


def _length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _entity(kind: str, text: str, needle: str, **extra) -> dict:
    return {
        "_": kind,
        "offset": _offset(text, needle),
        "length": _length(needle),
        **extra,
    }


def _art(path: Path, size: tuple[int, int], *, bars: bool = False) -> None:
    image = Image.new("RGB", size, (246, 248, 250))
    draw = ImageDraw.Draw(image)
    width, height = size
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(235 - int(25 * ratio), 243 - int(12 * ratio), 250),
        )
    if bars:
        margin = width // 8
        count = 7
        gap = width // (count * 3)
        bar_width = (width - 2 * margin - gap * (count - 1)) // count
        heights = [0.35, 0.62, 0.48, 0.8, 0.55, 0.9, 0.7]
        for index, ratio in enumerate(heights):
            x1 = margin + index * (bar_width + gap)
            y1 = int(height * (0.86 - ratio * 0.62))
            draw.rounded_rectangle(
                (x1, y1, x1 + bar_width, int(height * 0.86)),
                radius=8,
                fill=(22 + index * 12, 120 + index * 10, 205 - index * 8),
            )
        draw.rectangle((0, int(height * 0.86), width, height), fill=(220, 230, 240))
    else:
        draw.ellipse(
            (width * 0.12, height * 0.12, width * 0.88, height * 0.88),
            outline=(180, 205, 230),
            width=max(2, width // 120),
        )
        draw.ellipse(
            (width * 0.3, height * 0.3, width * 0.7, height * 0.7),
            fill=(255, 190, 80),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=90)
    image.copy().resize(
        (min(320, width), round(height * min(320, width) / width)),
        Image.Resampling.LANCZOS,
    ).save(path.with_name(path.stem + "_thumb.jpg"), "JPEG", quality=88)


def _pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 55 >> stream
BT /F1 18 Tf 40 120 Td (Eitaa export demo) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""
    path.write_bytes(content)


def _sticker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((18, 18, 238, 238), fill=(255, 210, 70, 255))
    draw.ellipse((54, 54, 202, 202), fill=(255, 245, 210, 255))
    draw.ellipse((92, 92, 164, 164), fill=(255, 170, 40, 255))
    image.save(path, "WEBP")


def _copy_assets() -> None:
    for name in ("css", "js", "images"):
        shutil.copytree(ASSETS / name, DEMO / name, dirs_exist_ok=True)


def build() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    for name in ("css", "js", "images", "photos", "video_files", "files", "voice_messages", "stickers"):
        target = DEMO / name
        if target.exists() and name not in {"css", "js", "images"}:
            shutil.rmtree(target)
    _copy_assets()

    photo_one = "photos/photo_1@19-08-2026_12-24-16.jpg"
    _art(DEMO / photo_one, (800, 370), bars=True)
    photo_two = "photos/photo_2@20-08-2026_20-49-10.jpg"
    _art(DEMO / photo_two, (600, 600))
    video = "video_files/video_1@20-08-2026_18-24-45.mp4"
    video_path = DEMO / video
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"")
    thumb_size = (720, 1280)
    thumb = Image.new("RGB", thumb_size, (22, 40, 62))
    ImageDraw.Draw(thumb).ellipse((260, 520, 460, 720), fill=(255, 255, 255))
    thumb.save(DEMO / "video_files/video_1@20-08-2026_18-24-45.mp4_thumb.jpg", "JPEG", quality=88)
    _pdf(DEMO / "files/report.pdf")
    _sticker(DEMO / "stickers/sticker_1.webp")
    (DEMO / "voice_messages").mkdir(exist_ok=True)
    (DEMO / "voice_messages/voice_1@19-08-2026_18-00-00.ogg").write_bytes(b"")

    chat = ChatInfo(
        kind="channel",
        peer_id=87197708,
        title=TITLE,
        username="yazd_oi",
        date=TS - 16 * 60,
        about="کانال آزمایشی برای نمایش قالب خروجی",
        members=42,
    )
    messages: list[Message] = [
        Message(
            id=1,
            date=TS - 16 * 60,
            action=f"Channel «{TITLE}» created",
        ),
        Message(
            id=2,
            date=TS,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("بسم الله الرحمن الرحیم"),
            signature="AmirReza PourAkhavan",
        ),
        Message(
            id=3,
            date=TS + 4 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text(
                "سلام.\nامروز بعد از نماز چهل دقیقه‌ای (تا قطع شدن برق) مستند را ادامه می‌دهیم ان‌شاءالله.\n"
                "این کانال در تلگرام هم راه‌اندازی شد:",
                [
                    _entity("messageEntityBold", "این کانال در تلگرام هم راه‌اندازی شد:", "این کانال در تلگرام هم راه‌اندازی شد:"),
                ],
            ),
            signature="AmirReza PourAkhavan",
        ),
        Message(
            id=4,
            date=TS + 7 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("ساعات مطالعاتی هفتهٔ گذشته"),
            signature="Hadi Faghih Khorasani",
            reactions=[("🔥", 2)],
            media=MediaInfo(
                kind="photo",
                path=photo_one,
                thumb=photo_one.replace(".jpg", "_thumb.jpg"),
                width=800,
                height=370,
            ),
        ),
        Message(
            id=5,
            date=TS + 7 * 60 + 20,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("زیرخاکی پیدا شده از آقای حمیدی."),
            signature="AmirReza PourAkhavan",
            reactions=[("🔥", 2)],
            media=MediaInfo(
                kind="video",
                path=video,
                thumb="video_files/video_1@20-08-2026_18-24-45.mp4_thumb.jpg",
                width=720,
                height=1280,
                duration=96,
                size=380000000,
                title="Video file",
            ),
        ),
        Message(
            id=6,
            date=TS + 10 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text(
                "شرکت در این آزمون‌ها برای همه الزامی‌ست.\n"
                "جزئیات: https://eitaa.com/whalexam_sup",
                [
                    _entity(
                        "messageEntityUrl",
                        "شرکت در این آزمون‌ها برای همه الزامی‌ست.\nجزئیات: https://eitaa.com/whalexam_sup",
                        "https://eitaa.com/whalexam_sup",
                    )
                ],
            ),
            signature="AmirReza PourAkhavan",
        ),
        Message(
            id=7,
            date=TS + 11 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("برای نمایندگان یزد در مرحله سوم دعا می‌کنیم."),
            signature="AmirReza PourAkhavan",
            reply_to_id=6,
            reactions=[("❤", 5), ("🔥", 3)],
        ),
        Message(
            id=8,
            date=TS + 12 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            forward=ForwardInfo(
                name="آزمون‌های المپیاد وال",
                username="whalexam",
                peer_id=555,
                date=TS + 9 * 60,
                author="AmirReza PourAkhavan",
            ),
            html_text=render_text(
                " آزمون شبیه‌ساز عملی فاینال دوره المپیاد کامپیوتر\n"
                "به‌منظور آمادگی هرچه بیشتر پذیرفته‌شدگان مرحله سوم، آزمون شبیه‌ساز فاینال عملی برگزار می‌شود.",
                [
                    _entity(
                        "messageEntityBold",
                        " آزمون شبیه‌ساز عملی فاینال دوره المپیاد کامپیوتر\nبه‌منظور آمادگی هرچه بیشتر پذیرفته‌شدگان مرحله سوم، آزمون شبیه‌ساز فاینال عملی برگزار می‌شود.",
                        "آزمون شبیه‌ساز عملی فاینال دوره المپیاد کامپیوتر",
                    )
                ],
            ),
            media=MediaInfo(
                kind="photo",
                path=photo_two,
                thumb=photo_two.replace(".jpg", "_thumb.jpg"),
                width=600,
                height=600,
            ),
        ),
        Message(
            id=9,
            date=TS + 13 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("ضبط‌شدهٔ کلاس و توضیحات:"),
            media=MediaInfo(
                kind="voice",
                path="voice_messages/voice_1@19-08-2026_18-00-00.ogg",
                duration=7,
                size=12595,
                title="Voice message",
            ),
        ),
        Message(
            id=10,
            date=TS + 14 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text("جزوهٔ امروز:"),
            media=MediaInfo(
                kind="document",
                path="files/report.pdf",
                size=1258291,
                title="report.pdf",
                mime="application/pdf",
            ),
        ),
        Message(
            id=11,
            date=TS + 15 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            media=MediaInfo(
                kind="sticker",
                path="stickers/sticker_1.webp",
                width=256,
                height=256,
                sticker_emoji="🌟",
            ),
        ),
        Message(
            id=12,
            date=TS + 16 * 60,
            sender_name=TITLE,
            sender_username="yazd_oi",
            sender_id=87197708,
            html_text=render_text(
                "نحوه درست و تمیز زدن کد سوالات sqrt decomposition:\n"
                "while (l < r)\n"
                "    if (l % S == 0 && l + S <= r) { l += S; }\n"
                "    else { ++l; }",
                [
                    _entity(
                        "messageEntityPre",
                        "نحوه درست و تمیز زدن کد سوالات sqrt decomposition:\nwhile (l < r)\n    if (l % S == 0 && l + S <= r) { l += S; }\n    else { ++l; }",
                        "while (l < r)\n    if (l % S == 0 && l + S <= r) { l += S; }\n    else { ++l; }",
                        language="cpp",
                    )
                ],
            ),
            signature="AmirReza PourAkhavan",
        ),
    ]

    next_day = TS + 20 * 3600
    messages.extend(
        [
            Message(
                id=13,
                date=next_day,
                sender_name=TITLE,
                sender_username="yazd_oi",
                sender_id=87197708,
                html_text=render_text(
                    "برداشت‌های دانش‌پژوهان از سورهٔ #حجرات\n"
                    "«إِنَّ أَكْرَمَكُمْ عِندَ اللَّهِ أَتْقَاكُمْ»\n"
                    "برای هماهنگی به @arpaaa پیام بدهید.",
                    [
                        _entity(
                            "messageEntityHashtag",
                            "برداشت‌های دانش‌پژوهان از سورهٔ #حجرات\n«إِنَّ أَكْرَمَكُمْ عِندَ اللَّهِ أَتْقَاكُمْ»\nبرای هماهنگی به @arpaaa پیام بدهید.",
                            "#حجرات",
                        ),
                        _entity(
                            "messageEntityMention",
                            "برداشت‌های دانش‌پژوهان از سورهٔ #حجرات\n«إِنَّ أَكْرَمَكُمْ عِندَ اللَّهِ أَتْقَاكُمْ»\nبرای هماهنگی به @arpaaa پیام بدهید.",
                            "@arpaaa",
                        ),
                        _entity(
                            "messageEntityBlockquote",
                            "برداشت‌های دانش‌پژوهان از سورهٔ #حجرات\n«إِنَّ أَكْرَمَكُمْ عِندَ اللَّهِ أَتْقَاكُمْ»\nبرای هماهنگی به @arpaaa پیام بدهید.",
                            "«إِنَّ أَكْرَمَكُمْ عِندَ اللَّهِ أَتْقَاكُمْ»",
                        ),
                    ],
                ),
                signature="AmirReza PourAkhavan",
                reactions=[("❤", 4)],
            ),
            Message(
                id=14,
                date=next_day + 3600,
                sender_name=TITLE,
                sender_username="yazd_oi",
                sender_id=87197708,
                html_text=render_text(
                    "یادآوری: کد `ranges::lower_bound(v, x)` جایگزین شکل قدیمی است."
                ),
                signature="AmirReza PourAkhavan",
            ),
        ]
    )

    # Add inline code entity to the last message.
    last_text = "یادآوری: کد ranges::lower_bound(v, x) جایگزین شکل قدیمی است."
    messages[-1].html_text = render_text(
        last_text,
        [_entity("messageEntityCode", last_text, "ranges::lower_bound(v, x)")],
    )

    html = Renderer(chat, messages, tz=TZ).render()
    (DEMO / "messages.html").write_text(html, encoding="utf-8")
    print(f"demo written to {DEMO / 'messages.html'}")


if __name__ == "__main__":
    build()
