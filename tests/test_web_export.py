from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eitaa_export.web import Runtime

TZ = ZoneInfo("Asia/Tehran")
CREATED = int(datetime(2026, 8, 19, 5, 0, tzinfo=TZ).timestamp())
CHAT = {
    "_": "channel",
    "id": 100,
    "access_hash": 5,
    "title": "Test channel",
    "username": "test_channel",
    "megagroup": False,
    "date": CREATED,
    "participants_count": 10,
}


class FakePeers:
    async def resolve(self, reference):
        return {"_": "inputPeerChannel", "channel_id": 100, "access_hash": 5}


class FakeMessages:
    async def history(self, peer, *, limit=100, offset_id=0, **kwargs):
        if offset_id:
            return {"messages": [], "chats": [CHAT], "users": []}
        return {
            "messages": [
                {
                    "_": "message",
                    "id": 2,
                    "date": CREATED + 60,
                    "message": "Hello",
                    "from_id": {"_": "peerChannel", "channel_id": 100},
                    "peer_id": {"_": "peerChannel", "channel_id": 100},
                },
                {
                    "_": "message",
                    "id": 1,
                    "date": CREATED,
                    "message": "",
                    "from_id": {"_": "peerChannel", "channel_id": 100},
                    "peer_id": {"_": "peerChannel", "channel_id": 100},
                    "media": {
                        "_": "messageMediaPhoto",
                        "photo": {
                            "_": "photo",
                            "id": 1,
                            "access_hash": 2,
                            "file_reference": b"",
                            "sizes": [
                                {"_": "photoSize", "type": "m", "w": 100, "h": 100, "size": 50},
                                {"_": "photoSize", "type": "x", "w": 800, "h": 800, "size": 500},
                            ],
                        },
                    },
                },
            ],
            "chats": [CHAT],
            "users": [],
        }


class FakeDialogs:
    async def info(self, peer):
        return {
            "full_chat": {"about": "Test", "participants_count": 10},
            "chats": [CHAT],
            "users": [],
        }


class FakeClient:
    def __init__(self) -> None:
        self.peers = FakePeers()
        self.messages = FakeMessages()
        self.dialogs = FakeDialogs()
        self.calls = 0

    async def invoke(self, method, params=None, **kwargs):
        if method != "upload.getFile":
            raise AssertionError(method)
        self.calls += 1
        limit = int((params or {})["limit"])
        return {"_": "upload.file", "bytes": b"z" * limit}

    async def close(self) -> None:
        return None


def test_gui_export_job_completes(tmp_path: Path) -> None:
    runtime = Runtime(tmp_path, profile="__test_gui__", quiet=True)
    runtime.start()
    try:
        runtime.call(runtime.client.close())  # type: ignore[union-attr]
        fake = FakeClient()
        runtime.client = fake  # type: ignore[assignment]
        snapshot = runtime.call(
            runtime.start_export(
                {
                    "target": "channel:100:5",
                    "title": "Test channel",
                    "output_name": "demo",
                    "media_types": ["photo"],
                    "max_file_size_bytes": 0,
                    "max_total_size_bytes": 0,
                    "timezone": "Asia/Tehran",
                    "include_created": False,
                }
            )
        )
        job_id = snapshot["id"]
        deadline = time.time() + 15
        final = snapshot
        while time.time() < deadline:
            job = runtime.get_job(job_id)
            assert job is not None
            final = job.snapshot()
            if final["status"] != "running":
                break
            time.sleep(0.05)
        assert final["status"] == "completed", final
        assert final["view_url"].endswith("/messages.html")
        assert final["stats"]["messages"] == 2
        assert (tmp_path / "demo" / "messages.html").is_file()
        assert list((tmp_path / "demo" / "photos").glob("photo_1@*.jpg"))
        assert fake.calls >= 2
        assert runtime.active_job_id is None
    finally:
        runtime.stop()
