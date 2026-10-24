from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from eitaa_export.exporter import Exporter, ExportOptions

TZ = ZoneInfo("Asia/Tehran")
CREATED = int(datetime(2026, 8, 19, 5, 0, tzinfo=TZ).timestamp())
FIRST = int(datetime(2026, 8, 19, 5, 16, 4, tzinfo=TZ).timestamp())
SECOND = int(datetime(2026, 8, 19, 10, 44, 39, tzinfo=TZ).timestamp())

CHAT = {
    "_": "channel",
    "id": 100,
    "access_hash": 5,
    "title": "کانال آزمایشی",
    "username": "test_channel",
    "megagroup": False,
    "date": CREATED,
    "participants_count": 12,
}


def _messages(include_created: bool, include_photo: bool = False) -> list[dict]:
    messages: list[dict] = []
    if include_created:
        messages.append(
            {
                "_": "messageService",
                "id": 1,
                "date": CREATED,
                "from_id": {"_": "peerChannel", "channel_id": 100},
                "peer_id": {"_": "peerChannel", "channel_id": 100},
                "action": {"_": "messageActionChannelCreate", "title": "کانال آزمایشی"},
            }
        )
    messages.append(
        {
            "_": "message",
            "id": 2,
            "date": FIRST,
            "message": "سلام دنیا",
            "from_id": {"_": "peerChannel", "channel_id": 100},
            "peer_id": {"_": "peerChannel", "channel_id": 100},
            "post_author": "Amir",
            "entities": [
                {"_": "messageEntityBold", "offset": 0, "length": 4},
            ],
        }
    )
    messages.append(
        {
            "_": "message",
            "id": 3,
            "date": SECOND,
            "message": "پاسخ به پیام",
            "from_id": {"_": "peerChannel", "channel_id": 100},
            "peer_id": {"_": "peerChannel", "channel_id": 100},
            "reply_to": {"_": "messageReplyHeader", "reply_to_msg_id": 2},
        }
    )
    if include_photo:
        messages.append(
            {
                "_": "message",
                "id": 4,
                "date": SECOND + 60,
                "message": "",
                "from_id": {"_": "peerChannel", "channel_id": 100},
                "peer_id": {"_": "peerChannel", "channel_id": 100},
                "media": {
                    "_": "messageMediaPhoto",
                    "photo": {
                        "_": "photo",
                        "id": 55,
                        "access_hash": 66,
                        "file_reference": b"",
                        "sizes": [
                            {"_": "photoSize", "type": "m", "w": 320, "h": 200, "size": 1200},
                            {"_": "photoSize", "type": "x", "w": 800, "h": 500, "size": 4200},
                        ],
                    },
                },
            }
        )
    return messages


class FakePeers:
    async def resolve(self, reference):
        return {"_": "inputPeerChannel", "channel_id": 100, "access_hash": 5}


class FakeMessages:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages

    async def history(self, peer, *, limit=100, offset_id=0, **kwargs):
        if offset_id:
            return {"_": "messages.channelMessages", "messages": [], "chats": [CHAT], "users": []}
        return {
            "_": "messages.channelMessages",
            "messages": list(reversed(self.messages)),
            "chats": [CHAT],
            "users": [],
        }


class FakeDialogs:
    async def info(self, peer):
        return {
            "_": "channels.channelFull",
            "full_chat": {"about": "دربارهٔ کانال", "participants_count": 12},
            "chats": [CHAT],
            "users": [],
        }


class FakeClient:
    def __init__(self, messages: list[dict], jpeg: bytes = b"") -> None:
        self.peers = FakePeers()
        self.messages = FakeMessages(messages)
        self.dialogs = FakeDialogs()
        self.jpeg = jpeg
        self.download_calls = 0

    async def invoke(self, method: str, params: dict | None = None, **kwargs):
        if method != "upload.getFile":
            raise AssertionError(f"unexpected method: {method}")
        self.download_calls += 1
        values = params or {}
        limit = int(values["limit"])
        return {"_": "upload.file", "bytes": b"\x00" * limit}


def _jpeg() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 40), (200, 120, 80)).save(buffer, "JPEG")
    return buffer.getvalue()


async def _export(tmp_path, messages, **kwargs):
    options = ExportOptions(output=tmp_path / "out", quiet=True, **kwargs)
    jpeg = _jpeg() if options.include_media else b""
    exporter = Exporter(FakeClient(messages, jpeg), options)
    return await exporter.export("test_channel")


@pytest.mark.asyncio
async def test_full_export_structure(tmp_path) -> None:
    stats = await _export(tmp_path, _messages(include_created=True), include_media=False)
    output = tmp_path / "out"
    html = (output / "messages.html").read_text(encoding="utf-8")
    assert "کانال آزمایشی" in html
    assert "19 August 2026" in html
    assert "Channel «کانال آزمایشی» created" in html
    assert "In reply to" in html
    assert '<strong>سلام</strong> دنیا' in html
    assert "Amir" in html
    assert stats.messages == 2
    assert stats.service_messages == 1
    assert (output / "css" / "style.css").is_file()
    assert (output / "js" / "script.js").is_file()
    assert (output / "images" / "back.png").is_file()
    assert not (output / "photos").exists()


@pytest.mark.asyncio
async def test_synthetic_creation_event(tmp_path) -> None:
    await _export(tmp_path, _messages(include_created=False), include_media=False)
    html = (tmp_path / "out" / "messages.html").read_text(encoding="utf-8")
    assert html.count("Channel «کانال آزمایشی» created") == 1
    assert 'id="message0"' in html


@pytest.mark.asyncio
async def test_photo_download(tmp_path) -> None:
    stats = await _export(
        tmp_path,
        _messages(include_created=True, include_photo=True),
        include_media=True,
    )
    output = tmp_path / "out"
    photos = list((output / "photos").glob("photo_1@*.jpg"))
    thumbs = list((output / "photos").glob("photo_1@*_thumb.jpg"))
    assert photos, "full photo was not written"
    assert thumbs, "thumbnail was not written"
    assert stats.photos == 1
    html = (output / "messages.html").read_text(encoding="utf-8")
    assert 'class="photo_wrap clearfix pull_left"' in html
    assert "photos/photo_1@19-08-2026_10-45-39.jpg" in html
    assert stats.bytes_written > 0


@pytest.mark.asyncio
async def test_time_window_filter(tmp_path) -> None:
    options_since = datetime(2026, 8, 19, 8, 0, tzinfo=TZ)
    stats = await _export(
        tmp_path,
        _messages(include_created=True),
        include_media=False,
        since=options_since,
    )
    html = (tmp_path / "out" / "messages.html").read_text(encoding="utf-8")
    assert "سلام دنیا" not in html
    assert "پاسخ به پیام" in html
    assert stats.messages == 1
