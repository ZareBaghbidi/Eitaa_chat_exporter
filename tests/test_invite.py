from __future__ import annotations

from pathlib import Path

import pytest

from eitaa_export.exporter import Exporter, ExportOptions


class InviteClient:
    def __init__(self, *, already: bool) -> None:
        self.already = already
        self.calls: list[tuple[str, dict]] = []
        self.imported = False

    async def invoke(self, method: str, params: dict | None = None, **kwargs):
        self.calls.append((method, params or {}))
        if method == "messages.checkChatInvite":
            if self.already or self.imported:
                return {
                    "_": "chatInviteAlready",
                    "chat": {
                        "_": "channel",
                        "id": 777,
                        "access_hash": 888,
                        "title": "کانال دعوتی",
                        "megagroup": False,
                    },
                }
            return {
                "_": "chatInvite",
                "title": "کانال دعوتی",
                "participants_count": 10,
            }
        if method == "messages.importChatInvite":
            self.imported = True
            return {"_": "updates", "updates": [], "users": [], "chats": []}
        raise AssertionError(f"unexpected method: {method}")


def _exporter(client: InviteClient, *, join: bool) -> Exporter:
    return Exporter(
        client,
        ExportOptions(output=Path("/tmp/never-written"), join_invite=join, quiet=True),
    )


@pytest.mark.asyncio
async def test_invite_already_member() -> None:
    client = InviteClient(already=True)
    peer = await _exporter(client, join=False)._resolve_peer("https://eitaa.com/+AbCdEf12")
    assert peer == {"_": "inputPeerChannel", "channel_id": 777, "access_hash": 888}
    assert client.calls[0][0] == "messages.checkChatInvite"
    assert client.calls[0][1]["hash"] == "AbCdEf12"


@pytest.mark.asyncio
async def test_invite_requires_join_flag() -> None:
    client = InviteClient(already=False)
    with pytest.raises(ValueError, match="--join"):
        await _exporter(client, join=False)._resolve_peer("https://eitaa.com/joinchat/AbCdEf12")


@pytest.mark.asyncio
async def test_invite_joins_when_requested() -> None:
    client = InviteClient(already=False)
    peer = await _exporter(client, join=True)._resolve_peer("https://eitaa.com/joinchat/AbCdEf12")
    assert peer == {"_": "inputPeerChannel", "channel_id": 777, "access_hash": 888}
    assert [call[0] for call in client.calls] == [
        "messages.checkChatInvite",
        "messages.importChatInvite",
        "messages.checkChatInvite",
    ]


def test_channel_post_links() -> None:
    from eitaa_export.exporter import channel_from_post_link

    assert channel_from_post_link("https://eitaa.com/yazd_oi/202") == "https://eitaa.com/yazd_oi"
    assert channel_from_post_link("eitaa.ir/yazd_oi/202/") == "https://eitaa.com/yazd_oi"
    assert channel_from_post_link("https://eitaa.com/joinchat/AbCd/202") is None
    assert channel_from_post_link("https://eitaa.com/yazd_oi") is None
