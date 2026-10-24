"""High-level export orchestration: fetch, download, render."""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eitaa_cli.errors import EitaaRPCError

from .media import MediaDownloader, MediaJob, MediaPlanner
from .models import ChatInfo, ExportStats, ForwardInfo, Message
from .render import Renderer, format_duration
from .text import render_text

_ASSETS = Path(__file__).with_name("assets")


@dataclass(slots=True)
class ExportOptions:
    output: Path
    include_media: bool = True
    include_stickers: bool = True
    limit: int | None = None
    since: datetime | None = None
    until: datetime | None = None
    timezone: str = "Asia/Tehran"
    include_created: bool = True
    join_invite: bool = False
    quiet: bool = False
    page_size: int = 100

    def tzinfo(self) -> tzinfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc


class EntityIndex:
    """Users and chats seen in any API response."""

    def __init__(self) -> None:
        self.users: dict[int, dict[str, Any]] = {}
        self.chats: dict[int, dict[str, Any]] = {}

    def update(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        for user in payload.get("users") or []:
            if isinstance(user, dict) and isinstance(user.get("id"), int):
                self.users[user["id"]] = user
        for chat in payload.get("chats") or []:
            if isinstance(chat, dict) and isinstance(chat.get("id"), int):
                self.chats[chat["id"]] = chat

    def user(self, user_id: int | None) -> dict[str, Any] | None:
        return self.users.get(user_id) if isinstance(user_id, int) else None

    def chat(self, chat_id: int | None) -> dict[str, Any] | None:
        return self.chats.get(chat_id) if isinstance(chat_id, int) else None


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _peer_key(peer: Any) -> tuple[str, int] | None:
    value = _obj(peer)
    predicate = _str(value.get("_"))
    if predicate in {"peerUser", "inputPeerUser", "user"}:
        return "user", _int(value.get("user_id") or value.get("id"))
    if predicate in {"peerChat", "inputPeerChat", "chat"}:
        return "chat", _int(value.get("chat_id") or value.get("id"))
    if predicate in {"peerChannel", "inputPeerChannel", "channel"}:
        return "channel", _int(value.get("channel_id") or value.get("id"))
    return None


def _entity_from_peer(peer: Any, index: EntityIndex) -> dict[str, Any] | None:
    key = _peer_key(peer)
    if key is None:
        return None
    kind, identifier = key
    if kind == "user":
        return index.user(identifier)
    return index.chat(identifier)


def _user_name(entity: dict[str, Any] | None) -> str:
    if not entity:
        return "Unknown"
    name = " ".join(
        part
        for part in (_str(entity.get("first_name")), _str(entity.get("last_name")))
        if part
    ).strip()
    return name or _str(entity.get("username")) or "Deleted account"


def _entity_name(entity: dict[str, Any] | None) -> str:
    if not entity:
        return "Unknown"
    return _str(entity.get("title")) or _user_name(entity)


def _entity_username(entity: dict[str, Any] | None) -> str | None:
    if not entity:
        return None
    username = _str(entity.get("username")).strip()
    return username or None


_INVITE_PATTERN = re.compile(
    r"(?:eitaa\.(?:com|ir)/)?(?:joinchat/|\+)([A-Za-z0-9_-]{4,})",
    re.IGNORECASE,
)
_POST_LINK_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?eitaa\.(?:com|ir)/([^/?#]+)/\d+/?$",
    re.IGNORECASE,
)


def channel_from_post_link(reference: str) -> str | None:
    """Turn ``eitaa.com/<username>/<post>`` into ``eitaa.com/<username>``."""

    match = _POST_LINK_PATTERN.match(reference.strip())
    if not match:
        return None
    handle = match.group(1)
    if handle.startswith(("joinchat", "+")):
        return None
    return f"https://eitaa.com/{handle}"


def invite_hash(reference: str) -> str | None:
    """Extract the hash from an Eitaa invite link such as ``eitaa.com/+AbCd``."""

    text = reference.strip()
    match = _INVITE_PATTERN.search(text)
    if match:
        return match.group(1)
    if text.startswith("+") and len(text) > 1:
        return text[1:]
    return None


def _input_peer_from_chat(chat: dict[str, Any]) -> dict[str, Any]:
    predicate = _str(chat.get("_"))
    if predicate in {"channel", "channelForbidden"}:
        access_hash = chat.get("access_hash")
        if not isinstance(access_hash, int):
            raise ValueError("invite link resolved to a channel without access hash")
        return {
            "_": "inputPeerChannel",
            "channel_id": _int(chat.get("id")),
            "access_hash": access_hash,
        }
    if predicate in {"chat", "chatForbidden"}:
        return {"_": "inputPeerChat", "chat_id": _int(chat.get("id"))}
    raise ValueError(f"unsupported invite chat type: {predicate!r}")


class Exporter:
    """Export one Eitaa peer into a Telegram-style HTML archive."""

    def __init__(self, client: Any, options: ExportOptions) -> None:
        self.client = client
        self.options = options
        self.tz = options.tzinfo()
        self.stats = ExportStats(output_dir=str(options.output))
        self.index = EntityIndex()
        self._warned: set[str] = set()

    def progress(self, text: str) -> None:
        if not self.options.quiet:
            print(text, file=sys.stderr)

    def warn(self, text: str) -> None:
        if text not in self._warned:
            self._warned.add(text)
            self.progress(f"warning: {text}")
            self.stats.warnings.append(text)

    async def export(self, peer_reference: str) -> ExportStats:
        options = self.options
        options.output.mkdir(parents=True, exist_ok=True)

        peer = await self._resolve_peer(peer_reference)
        self.progress(f"resolved {peer_reference}")
        info = await self._load_info(peer)
        if info is not None:
            self.index.update(info)

        raw_messages = await self._fetch_history(peer)
        messages = self._filter_and_sort(raw_messages)
        chat = self._build_chat(peer, info, peer_reference)
        self.progress(f"fetched {len(messages)} messages from «{chat.title}»")

        models, jobs = self._normalize(messages, chat)
        if options.include_created:
            created = self._created_event(models, chat)
            if created is not None:
                models.insert(0, created)

        if jobs:
            downloader = MediaDownloader(
                self.client,
                options.output,
                quiet=options.quiet,
                progress=self.progress,
            )
            await downloader.download_all(jobs)
            self.stats.bytes_written = downloader.bytes_written
            self.stats.warnings.extend(downloader.warnings)

        self._copy_assets()
        renderer = Renderer(chat, models, tz=self.tz)
        (options.output / "messages.html").write_text(renderer.render(), encoding="utf-8")
        self._count_stats(models)
        return self.stats

    async def _resolve_peer(self, reference: str) -> dict[str, Any]:
        """Resolve usernames, typed references, and Eitaa invite links."""

        reference = channel_from_post_link(reference) or reference
        code = invite_hash(reference)
        if code is None:
            return await self.client.peers.resolve(reference)
        invite = await self.client.invoke("messages.checkChatInvite", {"hash": code})
        if not isinstance(invite, dict):
            raise ValueError("پاسخ نامعتبر برای لینک عضویت")  # noqa: TRY004
        predicate = _str(invite.get("_"))
        if predicate in {"chatInviteAlready", "chatInvitePeek"}:
            chat = _obj(invite.get("chat"))
            if chat:
                self.index.chats[_int(chat.get("id"))] = chat
                return _input_peer_from_chat(chat)
            raise ValueError("لینک عضویت به گفت‌وگوی معتبری اشاره نمی‌کند")
        if predicate == "chatInvite":
            title = _str(invite.get("title"), "این گروه/کانال")
            if not self.options.join_invite:
                raise ValueError(
                    f"برای ورود به «{title}» باید عضو شوید. "
                    "دستور را با --join دوباره اجرا کنید یا ابتدا در ایتا عضو شوید."
                )
            await self.client.invoke("messages.importChatInvite", {"hash": code})
            joined = await self.client.invoke("messages.checkChatInvite", {"hash": code})
            chat = _obj(joined.get("chat")) if isinstance(joined, dict) else {}
            if not chat:
                raise ValueError(
                    "ورود انجام شد اما شناسهٔ گفت‌وگو در دسترس نیست؛ "
                    "چند لحظه بعد دوباره تلاش کنید."
                )
            self.index.chats[_int(chat.get("id"))] = chat
            return _input_peer_from_chat(chat)
        raise ValueError(f"پاسخ ناشناخته برای لینک عضویت: {predicate}")

    async def _load_info(self, peer: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await self.client.dialogs.info(peer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - chat metadata is optional
            self.warn(f"could not load chat details: {exc}")
            return None

    async def _fetch_history(self, peer: dict[str, Any]) -> list[dict[str, Any]]:
        page_size = max(1, min(100, self.options.page_size))
        limit = self.options.limit
        offset_id = 0
        previous_offset: int | None = None
        collected: dict[int, dict[str, Any]] = {}
        pages = 0

        while True:
            page = await self._history_page(peer, page_size, offset_id)
            pages += 1
            self.index.update(page)
            batch = [item for item in _list(page.get("messages")) if isinstance(item, dict)]
            if not batch:
                break
            ids = [item["id"] for item in batch if isinstance(item.get("id"), int)]
            for item in batch:
                if isinstance(item.get("id"), int) and _str(item.get("_")) in {
                    "message",
                    "messageService",
                }:
                    collected[item["id"]] = item
            if not self.options.quiet and (pages == 1 or pages % 5 == 0):
                self.progress(f"  fetched {len(collected)} messages...")
            if limit is not None and len(collected) >= limit:
                break
            if not ids:
                break
            next_offset = min(ids)
            if next_offset == previous_offset or next_offset == offset_id:
                break
            previous_offset = offset_id
            offset_id = next_offset
            if len(batch) < page_size:
                break

        return list(collected.values())

    async def _history_page(
        self,
        peer: dict[str, Any],
        page_size: int,
        offset_id: int,
    ) -> dict[str, Any]:
        for attempt in range(3):
            try:
                return await self.client.messages.history(
                    peer,
                    limit=page_size,
                    offset_id=offset_id,
                )
            except EitaaRPCError as exc:
                wait = exc.retry_after_seconds
                if wait is None or attempt >= 2:
                    raise
                wait = min(int(wait), 120)
                self.progress(f"  rate limited; waiting {wait}s before retrying...")
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")

    def _filter_and_sort(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        since_moment = self.options.since
        until_moment = self.options.until
        if since_moment is not None and since_moment.tzinfo is None:
            since_moment = since_moment.replace(tzinfo=self.tz)
        if until_moment is not None and until_moment.tzinfo is None:
            until_moment = until_moment.replace(tzinfo=self.tz)
        since = since_moment.timestamp() if since_moment else None
        until = until_moment.timestamp() if until_moment else None

        def keep(item: dict[str, Any]) -> bool:
            timestamp = _int(item.get("date"))
            return not (since is not None and timestamp < since) and not (
                until is not None and timestamp > until
            )

        filtered = [item for item in raw if keep(item)]
        filtered.sort(key=lambda item: (_int(item.get("date")), _int(item.get("id"))))
        if self.options.limit is not None:
            filtered = filtered[-self.options.limit :]
        return filtered

    def _build_chat(
        self,
        peer: dict[str, Any],
        info: dict[str, Any] | None,
        reference: str,
    ) -> ChatInfo:
        predicate = _str(peer.get("_"))
        if predicate == "inputPeerChannel":
            kind = "channel"
            peer_id = _int(peer.get("channel_id"))
            entity = self.index.chat(peer_id)
            if entity and (entity.get("megagroup") or entity.get("gigagroup")):
                kind = "supergroup"
            title = _entity_name(entity)
            username = _entity_username(entity)
        elif predicate == "inputPeerChat":
            kind = "group"
            peer_id = _int(peer.get("chat_id"))
            entity = self.index.chat(peer_id)
            title = _entity_name(entity)
            username = _entity_username(entity)
        elif predicate == "inputPeerUser":
            kind = "private"
            peer_id = _int(peer.get("user_id"))
            entity = self.index.user(peer_id)
            title = _user_name(entity)
            username = _entity_username(entity)
        else:
            kind = "private"
            peer_id = 0
            entity = None
            title = reference.lstrip("@") or "Saved Messages"
            username = None

        date = _int((entity or {}).get("date"))
        about = ""
        members = _int((entity or {}).get("participants_count"))
        if info:
            full = _obj(info.get("full_chat")) or _obj(info.get("full_user"))
            about = _str(full.get("about"))
            members = _int(full.get("participants_count"), members)
        if not title or title == "Unknown":
            title = reference.lstrip("@") or str(peer_id) or "Eitaa chat"
        return ChatInfo(
            kind=kind,
            peer_id=peer_id,
            title=title,
            username=username,
            date=date,
            about=about,
            members=members,
        )

    def _normalize(
        self,
        raw_messages: list[dict[str, Any]],
        chat: ChatInfo,
    ) -> tuple[list[Message], list[MediaJob]]:
        planner = (
            MediaPlanner(self.options.output, self.tz, include_media=self.options.include_media)
            if self.options.include_media
            else None
        )
        models: list[Message] = []
        jobs: list[MediaJob] = []
        current_key = ("channel" if chat.kind in {"channel", "supergroup"} else
                       "chat" if chat.kind == "group" else "user", chat.peer_id)

        for raw in raw_messages:
            message_id = _int(raw.get("id"))
            date = _int(raw.get("date"))
            if _str(raw.get("_")) == "messageService":
                action_obj = _obj(raw.get("action"))
                action = self._action_text(action_obj, chat)
                if not action:
                    continue
                service = Message(id=message_id, date=date, action=action)
                if planner is not None and _str(action_obj.get("_")) == "messageActionChatEditPhoto":
                    photo = _obj(action_obj.get("photo"))
                    if photo and _str(photo.get("_")) != "photoEmpty":
                        service_job = planner.plan(
                            {"_": "messageMediaPhoto", "photo": photo},
                            message_id,
                            date,
                        )
                        if service_job is not None:
                            service.media = service_job.info
                            jobs.append(service_job)
                models.append(service)
                continue

            sender_name, sender_username, sender_id = self._sender(raw, chat)
            forward = self._forward(raw)
            reply_to_id, other_chat = self._reply(raw, current_key)
            text = _str(raw.get("message"))
            html_text = render_text(text, _list(raw.get("entities")))
            media_info = None
            job = None
            if planner is not None:
                job = planner.plan(raw.get("media"), message_id, date)
                if job is not None:
                    if job.info.kind == "sticker" and not self.options.include_stickers:
                        job = None
                    else:
                        media_info = job.info
                        jobs.append(job)
            models.append(
                Message(
                    id=message_id,
                    date=date,
                    sender_name=sender_name,
                    sender_username=sender_username,
                    sender_id=sender_id,
                    signature=_str(raw.get("post_author")),
                    html_text=html_text,
                    plain_text=text,
                    reply_to_id=reply_to_id,
                    reply_other_chat=other_chat,
                    forward=forward,
                    media=media_info,
                    reactions=self._reactions(raw),
                    grouped_id=_int(raw.get("grouped_id")) or None,
                    out=bool(raw.get("out")),
                    pinned=bool(raw.get("pinned")),
                )
            )
        return models, jobs

    def _sender(self, raw: dict[str, Any], chat: ChatInfo) -> tuple[str, str | None, int | None]:
        if chat.is_broadcast:
            return chat.title, chat.username, chat.peer_id
        entity = _entity_from_peer(raw.get("from_id"), self.index)
        if entity is None:
            return chat.title, chat.username, chat.peer_id
        if _str(entity.get("_")) in {"user", "userEmpty"}:
            return _user_name(entity), _entity_username(entity), _int(entity.get("id"))
        return _entity_name(entity), _entity_username(entity), _int(entity.get("id"))

    def _forward(self, raw: dict[str, Any]) -> ForwardInfo | None:
        forward = _obj(raw.get("fwd_from"))
        if not forward:
            return None
        entity = _entity_from_peer(forward.get("from_id"), self.index)
        name = _str(forward.get("from_name")) or _entity_name(entity)
        username = _entity_username(entity)
        peer_id = _int(entity.get("id")) if entity else None
        return ForwardInfo(
            name=name or "Forwarded message",
            username=username,
            peer_id=peer_id,
            date=_int(forward.get("date")),
            author=_str(forward.get("post_author")) or None,
        )

    def _reply(self, raw: dict[str, Any], current_key: tuple[str, int]) -> tuple[int | None, bool]:
        reply = _obj(raw.get("reply_to"))
        if not reply:
            return None, False
        message_id = _int(reply.get("reply_to_msg_id")) or None
        peer_id = reply.get("reply_to_peer_id")
        other = False
        if peer_id:
            key = _peer_key(peer_id)
            other = key is not None and key != current_key
        return message_id, other

    def _reactions(self, raw: dict[str, Any]) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = []
        value = raw.get("reactions")
        items: Iterable[Any]
        if isinstance(value, dict):
            items = _list(value.get("results"))
        elif isinstance(value, list):
            items = value
        else:
            items = []
        for item in items:
            entry = _obj(item)
            reaction = _obj(entry.get("reaction"))
            emoji = _str(reaction.get("emoticon"))
            count = _int(entry.get("count"))
            if emoji and count > 0:
                result.append((emoji, count))
        return result

    def _action_text(self, action: dict[str, Any], chat: ChatInfo) -> str | None:
        kind = _str(action.get("_"))
        noun = "Channel" if chat.is_broadcast else "Group"
        if kind == "messageActionChatCreate":
            title = _str(action.get("title")) or chat.title
            return f"Group «{title}» created"
        if kind == "messageActionChannelCreate":
            title = _str(action.get("title")) or chat.title
            return f"Channel «{title}» created"
        if kind == "messageActionChatEditTitle":
            return f"{noun} title changed to «{_str(action.get('title'))}»"
        if kind == "messageActionChatEditPhoto":
            return f"{noun} photo changed"
        if kind == "messageActionChatDeletePhoto":
            return f"{noun} photo deleted"
        if kind == "messageActionChatAddUser":
            names = ", ".join(self._user_name(user_id) for user_id in _list(action.get("users")))
            return f"{names} joined the {noun.lower()}" if names else None
        if kind == "messageActionChatDeleteUser":
            return f"{self._user_name(action.get('user_id'))} left the {noun.lower()}"
        if kind == "messageActionChatJoinedByLink":
            name = self._user_name(action.get("inviter_id"))
            return f"{name} joined the {noun.lower()} via invite link"
        if kind == "messageActionPinMessage":
            return "Message pinned"
        if kind == "messageActionHistoryClear":
            return "History cleared"
        if kind == "messageActionSetMessagesTTL":
            seconds = _int(action.get("period"))
            if seconds:
                return f"Messages auto-delete timer changed to {format_duration(seconds, pad=False)}"
            return "Messages auto-delete timer disabled"
        if kind == "messageActionChatMigrateTo":
            return "Group migrated to supergroup"
        if kind == "messageActionChannelMigrateFrom":
            title = _str(action.get("title")) or "group"
            return f"Channel migrated from group «{title}»"
        if kind == "messageActionCustomAction":
            return _str(action.get("message")) or None
        if kind == "messageActionGameScore":
            return f"Game score: {_int(action.get('score'))}"
        if kind == "messageActionPhoneCall":
            duration = _int(action.get("duration"))
            suffix = f" ({format_duration(duration, pad=False)})" if duration else ""
            return f"Call{suffix}"
        if kind == "messageActionScreenshotTaken":
            return "Screenshot taken"
        if kind == "messageActionContactSignUp":
            return "Joined Eitaa"
        if kind == "messageActionBotAllowed":
            return "Bot allowed to send messages"
        if kind == "messageActionSetChatTheme":
            emoji = _str(action.get("emoticon"))
            return f"Chat theme changed to {emoji}".strip()
        if kind == "messageActionGroupCall":
            return "Voice chat started"
        if kind == "messageActionGroupCallScheduled":
            return "Voice chat scheduled"
        if kind == "messageActionInviteToGroupCall":
            names = ", ".join(self._user_name(user_id) for user_id in _list(action.get("users")))
            return f"{names} joined the voice chat" if names else None
        if kind == "messageActionEmpty":
            return None
        return None

    def _user_name(self, user_id: Any) -> str:
        return _user_name(self.index.user(_int(user_id)))

    def _created_event(self, models: list[Message], chat: ChatInfo) -> Message | None:
        if not chat.date or not (chat.is_group or chat.is_broadcast):
            return None
        if self.options.limit is not None:
            return None
        if self.options.since is not None or self.options.until is not None:
            return None
        for message in models:
            if message.action and ("created" in message.action):
                return None
            if message.action and message.action.startswith(("Channel «", "Group «")):
                return None
        noun = "Channel" if chat.is_broadcast else "Group"
        return Message(
            id=0,
            date=chat.date,
            action=f"{noun} «{chat.title}» created",
        )

    def _copy_assets(self) -> None:
        output = self.options.output
        for name in ("css", "js", "images"):
            source = _ASSETS / name
            destination = output / name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)

    def _count_stats(self, models: list[Message]) -> None:
        self.stats.messages = sum(1 for item in models if not item.is_service)
        self.stats.service_messages = sum(1 for item in models if item.is_service)
        for model in models:
            media = model.media
            if media is None:
                continue
            if media.kind == "photo":
                self.stats.photos += 1
            elif media.kind in {"video", "gif", "round"}:
                self.stats.videos += 1
            elif media.kind == "voice":
                self.stats.voice_messages += 1
            elif media.kind == "document":
                self.stats.documents += 1
            elif media.kind == "sticker":
                self.stats.stickers += 1
            else:
                self.stats.other_media += 1


__all__ = ["EntityIndex", "ExportOptions", "Exporter"]
