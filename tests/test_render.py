from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from eitaa_export.models import ChatInfo, ForwardInfo, MediaInfo, Message
from eitaa_export.render import Renderer

TZ = ZoneInfo("Asia/Tehran")


def _chat() -> ChatInfo:
    return ChatInfo(
        kind="channel",
        peer_id=100,
        title="کانال آزمایشی",
        username="test_channel",
        date=int(datetime(2026, 8, 19, 5, 0, tzinfo=TZ).timestamp()),
    )


def _ts(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 19, hour, minute, tzinfo=TZ).timestamp())


def test_header_dates_and_clickable_sender() -> None:
    chat = _chat()
    messages = [
        Message(
            id=1,
            date=_ts(5, 16),
            sender_name="کانال آزمایشی",
            sender_username="test_channel",
            sender_id=100,
            html_text="سلام",
        )
    ]
    html = Renderer(chat, messages, tz=TZ).render()
    assert "کانال آزمایشی" in html
    assert "19 August 2026" in html
    assert 'id="message-1"' in html
    assert 'id="message1"' in html
    assert 'title="19.08.2026 05:16:00 UTC+03:30"' in html
    assert '<a class="peer_link" href="https://eitaa.com/test_channel">' in html


def test_joined_messages_and_reply() -> None:
    chat = _chat()
    messages = [
        Message(
            id=1,
            date=_ts(10, 0),
            sender_name="کانال",
            sender_id=100,
            html_text="اول",
        ),
        Message(
            id=2,
            date=_ts(10, 1),
            sender_name="کانال",
            sender_id=100,
            html_text="دوم",
        ),
        Message(
            id=3,
            date=_ts(10, 2),
            sender_name="کانال",
            sender_id=100,
            html_text="پاسخ",
            reply_to_id=1,
        ),
    ]
    html = Renderer(chat, messages, tz=TZ).render()
    # A reply starts a new visual group, so the second sender line is expected.
    assert html.count('class="from_name"') == 2
    assert 'class="message default clearfix joined"' in html
    assert 'href="#go_to_message1"' in html
    assert "GoToMessage(1)" in html


def test_forward_contains_media_only_once() -> None:
    chat = _chat()
    message = Message(
        id=1,
        date=_ts(12, 0),
        sender_name="کانال",
        sender_id=100,
        html_text="متن فوروارد",
        forward=ForwardInfo(name="کانال مبدأ", username="source", peer_id=9, date=_ts(9, 0)),
        media=MediaInfo(
            kind="photo",
            path="photos/photo_1@19-08-2026_12-00-00.jpg",
            thumb="photos/photo_1@19-08-2026_12-00-00_thumb.jpg",
            width=260,
            height=120,
        ),
    )
    html = Renderer(chat, [message], tz=TZ).render()
    assert html.count('class="media_wrap clearfix"') == 1
    assert html.count("متن فوروارد") == 1
    assert '<div class="forwarded body">' in html
    assert 'href="https://eitaa.com/source"' in html
    assert "19.08.2026 09:00:00" in html


def test_media_blocks() -> None:
    chat = _chat()
    video = MediaInfo(
        kind="video",
        path="video_files/video_1.mp4",
        thumb="video_files/video_1_thumb.jpg",
        width=146,
        height=260,
        duration=96,
    )
    voice = MediaInfo(
        kind="voice",
        path="voice_messages/voice_1.ogg",
        duration=7,
        size=12595,
        title="Voice message",
    )
    document = MediaInfo(
        kind="document",
        path="files/report.pdf",
        size=1258291,
        title="report.pdf",
        mime="application/pdf",
    )
    messages = [
        Message(id=1, date=_ts(1, 0), sender_name="c", sender_id=100, media=video),
        Message(id=2, date=_ts(2, 0), sender_name="c", sender_id=100, media=voice),
        Message(id=3, date=_ts(3, 0), sender_name="c", sender_id=100, media=document),
    ]
    html = Renderer(chat, messages, tz=TZ).render()
    assert 'class="video_file_wrap clearfix pull_left"' in html
    assert "01:36" in html
    assert 'class="media clearfix pull_left media_voice_message"' in html
    assert "0:07" in html
    assert 'class="media clearfix pull_left media_file"' in html
    assert "PDF document" in html
    assert "1.2 MB" in html


def test_reactions_and_signature() -> None:
    chat = _chat()
    message = Message(
        id=1,
        date=_ts(1, 0),
        sender_name="کانال",
        sender_id=100,
        html_text="متن",
        signature="Amir",
        reactions=[("🔥", 2), ("❤", 1)],
    )
    html = Renderer(chat, [message], tz=TZ).render()
    assert '<div class="signature details">' in html
    assert "Amir" in html
    assert '<span class="emoji">🔥</span>' in html
    assert '<span class="count">2</span>' in html


def test_service_photo_change() -> None:
    chat = _chat()
    message = Message(
        id=2,
        date=_ts(5, 15),
        action="Channel photo changed",
        media=MediaInfo(
            kind="photo",
            path="photos/photo_1@19-08-2026_05-15-00.jpg",
            thumb="photos/photo_1@19-08-2026_05-15-00_thumb.jpg",
            width=640,
            height=640,
        ),
    )
    html = Renderer(chat, [message], tz=TZ).render()
    assert "Channel photo changed" in html
    assert '<img class="userpic"' in html
    assert "photo_1@19-08-2026_05-15-00_thumb.jpg" in html
