from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from eitaa_export.media import MediaPlanner

TZ = ZoneInfo("Asia/Tehran")
TS = int(datetime(2026, 8, 19, 5, 16, 4, tzinfo=TZ).timestamp())


def _photo(photo_id: int = 1) -> dict:
    return {
        "_": "messageMediaPhoto",
        "photo": {
            "_": "photo",
            "id": photo_id,
            "access_hash": 42,
            "file_reference": b"ref",
            "sizes": [
                {"_": "photoSize", "type": "s", "w": 320, "h": 200, "size": 1000},
                {"_": "photoSize", "type": "x", "w": 1280, "h": 800, "size": 50000},
            ],
        },
    }


def test_photo_naming_and_pagination(tmp_path) -> None:
    planner = MediaPlanner(tmp_path, TZ)
    first = planner.plan(_photo(1), 1, TS)
    second = planner.plan(_photo(2), 2, TS)
    assert first is not None and second is not None
    assert first.info.path == "photos/photo_1@19-08-2026_05-16-04.jpg"
    assert first.info.thumb == "photos/photo_1@19-08-2026_05-16-04_thumb.jpg"
    assert second.info.path == "photos/photo_2@19-08-2026_05-16-04.jpg"
    assert first.info.width == 1280
    assert first.info.height == 800
    assert first.location is not None
    assert first.location["thumb_size"] == "x"
    assert first.thumb_location is not None
    assert first.thumb_location["thumb_size"] == "s"


def test_video_document_with_original_name(tmp_path) -> None:
    planner = MediaPlanner(tmp_path, TZ)
    raw = {
        "_": "messageMediaDocument",
        "document": {
            "_": "document",
            "id": 7,
            "access_hash": 8,
            "file_reference": b"",
            "mime_type": "video/mp4",
            "size": 123456,
            "attributes": [
                {"_": "documentAttributeFilename", "file_name": "../../clip.mp4"},
                {"_": "documentAttributeVideo", "duration": 96, "w": 720, "h": 1280},
            ],
            "thumbs": [{"_": "photoSize", "type": "m", "w": 320, "h": 569, "size": 9000}],
        },
    }
    job = planner.plan(raw, 3, TS)
    assert job is not None
    assert job.info.kind == "video"
    assert job.info.path == "video_files/clip.mp4"
    assert job.info.thumb == "video_files/clip.mp4_thumb.jpg"
    assert job.info.duration == 96
    assert job.thumb_location is not None


def test_animated_sticker_names(tmp_path) -> None:
    planner = MediaPlanner(tmp_path, TZ)
    raw = {
        "_": "messageMediaDocument",
        "document": {
            "_": "document",
            "id": 9,
            "access_hash": 10,
            "file_reference": b"",
            "mime_type": "application/x-tgsticker",
            "size": 20000,
            "attributes": [
                {"_": "documentAttributeSticker", "alt": "✔️"},
            ],
        },
    }
    first = planner.plan(raw, 4, TS)
    second = planner.plan(raw, 5, TS)
    assert first is not None and second is not None
    assert first.info.path == "stickers/AnimatedSticker.tgs"
    assert second.info.path == "stickers/AnimatedSticker (1).tgs"


def test_voice_and_document_media(tmp_path) -> None:
    planner = MediaPlanner(tmp_path, TZ)
    voice = {
        "_": "messageMediaDocument",
        "document": {
            "_": "document",
            "id": 11,
            "access_hash": 12,
            "file_reference": b"",
            "mime_type": "audio/ogg",
            "size": 5000,
            "attributes": [{"_": "documentAttributeAudio", "voice": True, "duration": 7}],
        },
    }
    job = planner.plan(voice, 6, TS)
    assert job is not None
    assert job.info.kind == "voice"
    assert job.info.path == "voice_messages/voice_1@19-08-2026_05-16-04.ogg"

    document = {
        "_": "messageMediaDocument",
        "document": {
            "_": "document",
            "id": 13,
            "access_hash": 14,
            "file_reference": b"",
            "mime_type": "application/pdf",
            "size": 1258291,
            "attributes": [{"_": "documentAttributeFilename", "file_name": "report.pdf"}],
        },
    }
    job = planner.plan(document, 7, TS)
    assert job is not None
    assert job.info.kind == "document"
    assert job.info.path == "files/report.pdf"


def test_location_and_poll(tmp_path) -> None:
    planner = MediaPlanner(tmp_path, TZ)
    location = planner.plan(
        {"_": "messageMediaGeo", "geo": {"_": "geoPoint", "lat": 31.8974, "long": 54.3569}},
        8,
        TS,
    )
    assert location is not None
    assert location.info.kind == "location"
    assert "31.897400" in location.info.description

    poll = planner.plan(
        {
            "_": "messageMediaPoll",
            "poll": {
                "_": "poll",
                "question": "کدام؟",
                "answers": [{"_": "pollAnswer", "text": "اول"}, {"_": "pollAnswer", "text": "دوم"}],
            },
            "results": {},
        },
        9,
        TS,
    )
    assert poll is not None
    assert poll.info.kind == "poll"
    assert "اول" in poll.info.description


def test_invite_hash_parsing() -> None:
    from eitaa_export.exporter import invite_hash

    assert invite_hash("https://eitaa.com/+AbCdEf123") == "AbCdEf123"
    assert invite_hash("https://eitaa.com/joinchat/AbCdEf123") == "AbCdEf123"
    assert invite_hash("eitaa.ir/joinchat/AbCdEf123") == "AbCdEf123"
    assert invite_hash("+AbCdEf123") == "AbCdEf123"
    assert invite_hash("@yazd_oi") is None
    assert invite_hash("https://eitaa.com/yazd_oi") is None
