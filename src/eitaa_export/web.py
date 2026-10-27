"""Local web GUI for the Eitaa HTML exporter.

The GUI is a small, dependency-free HTTP server. All Eitaa work runs on a
dedicated asyncio loop in a background thread; browser requests are bridged to
that loop with ``run_coroutine_threadsafe``. The page itself is plain HTML,
CSS, and JavaScript and is served from ``eitaa_export/web``.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eitaa_cli import EitaaClient
from eitaa_cli.errors import (
    AuthenticationRequired,
    EitaaError,
    EitaaRPCError,
    OtpError,
    PeerResolutionError,
)

from . import __version__
from .exporter import Exporter, ExportOptions, channel_from_post_link, invite_hash

_WEB_ROOT = Path(__file__).with_name("web")
_DEFAULT_MEDIA = {"photo", "video", "voice", "audio", "document", "sticker", "gif"}
_MEDIA_LABELS = {
    "photo": "Photo",
    "video": "Video",
    "voice": "Voice message",
    "audio": "Audio file",
    "document": "File",
    "sticker": "Sticker",
    "gif": "GIF",
}
_SAFE_NAME = re.compile(r"[^\w\u0600-\u06FF.-]+", re.UNICODE)


class ActiveExportError(Exception):
    """Raised when a second export is requested while one is running."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _slug(value: str, fallback: str = "export") -> str:
    text = value.strip()
    text = text.replace("\\", "_").replace("/", "_")
    text = _SAFE_NAME.sub("_", text).strip(" ._-")
    text = text[:80].strip(" ._-")
    return text or fallback


def _default_output_name(target: str) -> str:
    text = channel_from_post_link(target.strip()) or target.strip()
    text = re.sub(r"^https?://(?:www\.)?eitaa\.(?:com|ir)/", "", text, flags=re.IGNORECASE)
    text = text.lstrip("@+/").strip("/")
    text = text.removeprefix("joinchat/")
    return _slug(text, "export")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _display_name(entity: dict[str, Any] | None) -> str:
    if not entity:
        return "Unknown"
    name = " ".join(
        part
        for part in (str(entity.get("first_name") or ""), str(entity.get("last_name") or ""))
        if part
    ).strip()
    return name or str(entity.get("username") or "") or "Deleted account"


def _entity_title(entity: dict[str, Any] | None) -> str:
    if not entity:
        return "Unknown"
    return str(entity.get("title") or "") or _display_name(entity)


def _entity_username(entity: dict[str, Any] | None) -> str | None:
    if not entity:
        return None
    username = str(entity.get("username") or "").strip()
    return username or None


def _initials(name: str) -> str:
    parts = [part for part in name.strip().split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


@dataclass(slots=True)
class Job:
    id: str
    target: str
    title: str
    output_dir: Path
    status: str = "running"
    phase: str = "starting"
    message: str = "Starting export..."
    overall_done: int = 0
    overall_total: int = 0
    files_done: int = 0
    files_total: int = 0
    file_label: str = ""
    file_done: int = 0
    file_total: int = 0
    file_status: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] | None = None
    error: str | None = None
    view_url: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    task: asyncio.Task[Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def add_event(self, text: str) -> None:
        with self.lock:
            self.events.append({"time": time.time(), "text": text})
            if len(self.events) > 120:
                self.events = self.events[-120:]
            self.message = text
            lowered = text.lower()
            if lowered.startswith(("resolved", "fetched")):
                self.phase = "fetching"
            elif "rendering" in lowered or "finalizing" in lowered:
                self.phase = "rendering"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            overall_percent = (
                100.0 * self.overall_done / self.overall_total
                if self.overall_total > 0
                else (100.0 if self.status == "completed" else 0.0)
            )
            file_percent = (
                100.0 * self.file_done / self.file_total if self.file_total > 0 else 0.0
            )
            return _jsonable(
                {
                    "id": self.id,
                    "target": self.target,
                    "title": self.title,
                    "status": self.status,
                    "phase": self.phase,
                    "message": self.message,
                    "started_at": self.started_at,
                    "finished_at": self.finished_at,
                    "output_dir": str(self.output_dir),
                    "view_url": self.view_url,
                    "overall": {
                        "done": self.overall_done,
                        "total": self.overall_total,
                        "percent": round(min(100.0, overall_percent), 1),
                        "files_done": self.files_done,
                        "files_total": self.files_total,
                    },
                    "file": {
                        "label": self.file_label,
                        "done": self.file_done,
                        "total": self.file_total,
                        "percent": round(min(100.0, file_percent), 1),
                        "status": self.file_status,
                    },
                    "events": list(self.events[-40:]),
                    "stats": self.stats,
                    "error": self.error,
                    "cancel_requested": self.cancel_requested,
                }
            )


class WebProgress:
    """Adapter from the downloader's progress API to a :class:`Job`."""

    def __init__(self, job: Job, total_files: int, total_bytes: int) -> None:
        self.job = job
        self.total_files = total_files
        self.total_bytes = max(1, total_bytes)
        self._advanced = 0
        self._task_total = 1
        with job.lock:
            job.files_total = total_files
            job.overall_total = self.total_bytes
            job.overall_done = 0
            job.files_done = 0
            job.phase = "downloading"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        del exc
        self.finish_all()

    def begin_file(self, index: int, label: str, total: int) -> None:
        self._advanced = 0
        self._task_total = max(1, total)
        with self.job.lock:
            self.job.phase = "downloading"
            self.job.file_label = label
            self.job.file_done = 0
            self.job.file_total = self._task_total
            self.job.file_status = "downloading"
            self.job.files_done = max(0, index - 1)

    def advance(self, amount: int) -> None:
        if amount <= 0:
            return
        self._advanced += amount
        with self.job.lock:
            self.job.file_done = min(self._task_total, self.job.file_done + amount)
            self.job.overall_done = min(
                self.job.overall_total,
                self.job.overall_done + amount,
            )

    def finish(self, *, success: bool = True, status: str = "done") -> None:
        with self.job.lock:
            self.job.file_done = self._task_total
            self.job.file_status = status if success else "failed"
            if success and self._advanced < self._task_total:
                extra = self._task_total - self._advanced
                self.job.overall_done = min(
                    self.job.overall_total,
                    self.job.overall_done + extra,
                )
            self.job.files_done += 1

    def finish_all(self) -> None:
        with self.job.lock:
            self.job.overall_done = self.job.overall_total
            if self.job.files_total:
                self.job.files_done = self.job.files_total

    def note(self, text: str) -> None:
        self.job.add_event(text)

    def message(self, text: str) -> None:
        self.job.add_event(text)


class Runtime:
    """Owns the Eitaa client, the event loop, and export jobs."""

    def __init__(
        self,
        export_root: Path,
        *,
        profile: str | None = None,
        quiet: bool = False,
    ) -> None:
        self.export_root = export_root.expanduser().resolve()
        self.profile = profile
        self.quiet = quiet
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop_main, daemon=True)
        self.client: EitaaClient | None = None
        self.jobs: dict[str, Job] = {}
        self.active_job_id: str | None = None
        self._lock = threading.RLock()
        self._started = False
        self._print_lock = threading.Lock()

    # -- infrastructure ----------------------------------------------------

    def _loop_main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        self._started = True
        self.call(self._bootstrap(), timeout=60)

    async def _bootstrap(self) -> None:
        self.client = await EitaaClient.create(
            require_auth=False,
            profile=self.profile,
        )

    def call(self, coroutine: Any, *, timeout: float = 120.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError("operation timed out") from exc

    def stop(self) -> None:
        if not self._started:
            return
        try:
            if self.client is not None:
                self.call(self.client.close(), timeout=10)
        except Exception:  # noqa: BLE001, S110 - shutdown is best effort
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self._started = False

    def _require_client(self) -> EitaaClient:
        if self.client is None:
            raise RuntimeError("client is not ready")
        return self.client

    # -- auth --------------------------------------------------------------

    async def _me(self) -> dict[str, Any] | None:
        client = self._require_client()
        profile = client.profile
        if not profile.authenticated:
            return None
        raw_user: Any = profile.user
        user: dict[str, Any] = dict(raw_user) if isinstance(raw_user, dict) else {}
        if not user.get("first_name") and not user.get("username"):
            try:
                result = await client.invoke(
                    "users.getFullUser",
                    {"id": {"_": "inputUserSelf"}},
                )
                if isinstance(result, dict):
                    users = result.get("users") or []
                    if users and isinstance(users[0], dict):
                        user = dict(users[0])
            except EitaaError:
                user = {}
        name = _display_name(user) if user else ""
        if not name or name == "Unknown":
            name = profile.phone_number or profile.name
        phone = profile.phone_number or str(user.get("phone") or "")
        user_id = user.get("id")
        return {
            "name": name,
            "username": _entity_username(user),
            "phone": phone,
            "id": user_id,
            "initials": _initials(name),
            "color": (abs(_safe_int(user_id)) % 7) + 1,
            "profile": profile.name,
        }

    async def status(self) -> dict[str, Any]:
        me = await self._me()
        return {
            "authenticated": me is not None,
            "me": me,
            "active_job_id": self.active_job_id,
            "export_root": str(self.export_root),
            "version": __version__,
        }

    async def request_code(self, phone: str) -> dict[str, Any]:
        client = self._require_client()
        challenge = await client.auth.request_code(phone)
        return {
            "phone_number": challenge.phone_number,
            "phone_code_hash": challenge.phone_code_hash,
            "delivery": getattr(challenge.delivery, "value", str(challenge.delivery)),
            "next_delivery": (
                getattr(challenge.next_delivery, "value", str(challenge.next_delivery))
                if challenge.next_delivery
                else None
            ),
            "timeout_seconds": challenge.timeout_seconds,
            "code_length": challenge.code_length,
            "flash_call_pattern": challenge.flash_call_pattern,
        }

    async def resend_code(self, phone: str, phone_code_hash: str) -> dict[str, Any]:
        client = self._require_client()
        challenge = await client.auth.resend_code(phone, phone_code_hash)
        return {
            "phone_number": challenge.phone_number,
            "phone_code_hash": challenge.phone_code_hash,
            "delivery": getattr(challenge.delivery, "value", str(challenge.delivery)),
            "next_delivery": (
                getattr(challenge.next_delivery, "value", str(challenge.next_delivery))
                if challenge.next_delivery
                else None
            ),
            "timeout_seconds": challenge.timeout_seconds,
            "code_length": challenge.code_length,
        }

    async def verify_code(self, phone: str, phone_code_hash: str, code: str) -> dict[str, Any]:
        client = self._require_client()
        await client.auth.sign_in(
            phone,
            phone_code_hash,
            code,
            profile_name=self.profile,
        )
        me = await self._me()
        return {
            "authenticated": me is not None,
            "me": me,
            "active_job_id": self.active_job_id,
            "export_root": str(self.export_root),
            "version": __version__,
        }

    async def logout(self) -> dict[str, Any]:
        if self.active_job_id is not None:
            raise ValueError("Cancel the active export before signing out.")
        client = self._require_client()
        try:
            await client.auth.logout()
        except EitaaError:
            if client.profile.authenticated:
                await client.store.adelete(client.profile.name)
        finally:
            await client.close()
            self.client = await EitaaClient.create(
                require_auth=False,
                profile=self.profile,
            )
        return await self.status()

    # -- chats -------------------------------------------------------------

    def _reference(self, kind: str, entity: dict[str, Any] | None, identifier: int) -> str:
        if entity and isinstance(entity.get("access_hash"), int):
            return f"{kind}:{identifier}:{entity['access_hash']}"
        return f"{kind}:{identifier}"

    @staticmethod
    def _preview(message: dict[str, Any] | None) -> str:
        if not message:
            return "No messages yet"
        if str(message.get("_")) == "messageService":
            return "Service message"
        text = str(message.get("message") or "").replace("\n", " ").strip()
        if text:
            return text[:90]
        media = message.get("media")
        if isinstance(media, dict):
            predicate = str(media.get("_") or "")
            if predicate == "messageMediaPhoto":
                return "Photo"
            if predicate == "messageMediaDocument":
                return "File"
            if predicate in {"messageMediaGeo", "messageMediaVenue"}:
                return "Location"
            if predicate == "messageMediaPoll":
                return "Poll"
        return "Message"

    async def chats(self, limit: int = 100, query: str = "") -> list[dict[str, Any]]:
        client = self._require_client()
        result = await client.dialogs.list(max(1, min(200, limit)), query=query or None)
        messages: dict[int, dict[str, Any]] = {}
        users: dict[int, dict[str, Any]] = {}
        chats: dict[int, dict[str, Any]] = {}
        for message_item in result.get("messages") or []:
            if isinstance(message_item, dict) and isinstance(message_item.get("id"), int):
                messages[message_item["id"]] = dict(message_item)
        for user_item in result.get("users") or []:
            if isinstance(user_item, dict) and isinstance(user_item.get("id"), int):
                users[user_item["id"]] = dict(user_item)
        for chat_item in result.get("chats") or []:
            if isinstance(chat_item, dict) and isinstance(chat_item.get("id"), int):
                chats[chat_item["id"]] = dict(chat_item)
        rows: list[dict[str, Any]] = []
        for dialog in result.get("dialogs") or []:
            if not isinstance(dialog, dict):
                continue
            peer = dialog.get("peer")
            if not isinstance(peer, dict):
                continue
            predicate = str(peer.get("_") or "")
            identifier = (
                peer.get("user_id")
                if predicate == "peerUser"
                else peer.get("chat_id")
                if predicate == "peerChat"
                else peer.get("channel_id")
            )
            if not isinstance(identifier, int):
                continue
            if predicate == "peerUser":
                entity = users.get(identifier)
                kind = "private"
                reference = self._reference("user", entity, identifier)
                title = _display_name(entity)
            elif predicate == "peerChat":
                entity = chats.get(identifier)
                kind = "group"
                reference = f"chat:{identifier}"
                title = _entity_title(entity)
            else:
                entity = chats.get(identifier)
                kind = "supergroup" if (entity or {}).get("megagroup") else "channel"
                reference = self._reference("channel", entity, identifier)
                title = _entity_title(entity)
            top_id = dialog.get("top_message")
            top = messages.get(top_id) if isinstance(top_id, int) else None
            date = top.get("date") if isinstance(top, dict) else 0
            username = _entity_username(entity)
            rows.append(
                {
                    "id": identifier,
                    "kind": kind,
                    "title": title or "Unknown",
                    "username": username,
                    "reference": reference,
                    "initials": _initials(title or "?"),
                    "color": (abs(identifier) % 7) + 1,
                    "unread": _safe_int(dialog.get("unread_count")),
                    "pinned": bool(dialog.get("pinned")),
                    "date": date,
                    "preview": self._preview(top),
                    "is_bot": bool((entity or {}).get("bot")),
                    "verified": bool((entity or {}).get("verified")),
                    "members": _safe_int((entity or {}).get("participants_count")),
                }
            )
        return rows

    async def resolve(self, target: str) -> dict[str, Any]:
        target = target.strip()
        if not target:
            raise ValueError("Enter a link, @username, or peer reference.")
        normalized = channel_from_post_link(target) or target
        code = invite_hash(normalized)
        if code is not None:
            client = self._require_client()
            invite = await client.invoke("messages.checkChatInvite", {"hash": code})
            if not isinstance(invite, dict):
                raise ValueError("Invalid invite response.")
            if str(invite.get("_")) in {"chatInviteAlready", "chatInvitePeek"}:
                chat = invite.get("chat")
                if not isinstance(chat, dict):
                    raise ValueError("Invite does not point to a chat.")
                return self._describe_chat(chat, target, requires_join=False)
            if str(invite.get("_")) == "chatInvite":
                title = str(invite.get("title") or "this chat")
                kind = (
                    "channel"
                    if invite.get("broadcast") and not invite.get("megagroup")
                    else "group"
                )
                return {
                    "title": title,
                    "username": None,
                    "kind": kind,
                    "reference": target,
                    "peer_id": None,
                    "requires_join": True,
                }
            raise ValueError("Unsupported invite response.")
        client = self._require_client()
        peer = await client.peers.resolve(normalized)
        info = await client.dialogs.info(peer)
        entity: dict[str, Any] | None = None
        if isinstance(info, dict):
            info_obj = cast(dict[str, Any], info)
            peer_ids = {
                peer.get("channel_id"),
                peer.get("chat_id"),
                peer.get("user_id"),
            }
            for item in info_obj.get("chats") or []:
                if isinstance(item, dict) and item.get("id") in peer_ids:
                    entity = item
                    break
            if entity is None:
                for item in info_obj.get("users") or []:
                    if isinstance(item, dict) and item.get("id") == peer.get("user_id"):
                        entity = item
                        break
        if entity is None:
            return {
                "title": normalized,
                "username": None,
                "kind": "channel" if str(peer.get("_")) == "inputPeerChannel" else "chat",
                "reference": normalized,
                "peer_id": peer.get("channel_id") or peer.get("user_id"),
                "requires_join": False,
            }
        return self._describe_chat(entity, normalized, requires_join=False)

    def _describe_chat(
        self,
        entity: dict[str, Any],
        fallback_reference: str,
        *,
        requires_join: bool,
    ) -> dict[str, Any]:
        predicate = str(entity.get("_") or "")
        identifier = _safe_int(entity.get("id"))
        if predicate in {"channel", "channelForbidden"}:
            kind = "supergroup" if entity.get("megagroup") else "channel"
            reference = self._reference("channel", entity, identifier)
            title = _entity_title(entity)
        elif predicate in {"chat", "chatForbidden"}:
            kind = "group"
            reference = f"chat:{identifier}"
            title = _entity_title(entity)
        elif predicate in {"user", "userEmpty"}:
            kind = "private"
            reference = self._reference("user", entity, identifier)
            title = _display_name(entity)
        else:
            kind = "chat"
            reference = fallback_reference
            title = _entity_title(entity)
        return {
            "title": title,
            "username": _entity_username(entity),
            "kind": kind,
            "reference": reference,
            "peer_id": identifier,
            "requires_join": requires_join,
        }

    # -- export jobs -------------------------------------------------------

    async def start_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.active_job_id is not None:
                active = self.jobs.get(self.active_job_id)
                if active is not None and active.status == "running":
                    raise ActiveExportError("An export is already running.")
        target = str(payload.get("target") or "").strip()
        if not target:
            raise ValueError("Enter a link, @username, or peer reference.")
        media_types = payload.get("media_types")
        if isinstance(media_types, list):
            allowed = {str(item) for item in media_types if str(item) in _DEFAULT_MEDIA}
        else:
            allowed = set(_DEFAULT_MEDIA)
        output_name = _slug(
            str(payload.get("output_name") or ""),
            _default_output_name(target),
        )
        output_dir = self.export_root / output_name
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError("The output path exists and is not a directory.")
        timezone_name = str(payload.get("timezone") or "Asia/Tehran")
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Invalid timezone: {timezone_name}") from exc
        since = self._parse_date(payload.get("since"), zone, end_of_day=False)
        until = self._parse_date(payload.get("until"), zone, end_of_day=True)
        if since and until and since > until:
            raise ValueError("Start date must be earlier than end date.")
        limit = payload.get("limit")
        limit_value = _safe_int(limit, 0) if limit not in (None, "") else 0
        if limit not in (None, "") and limit_value < 1:
            raise ValueError("Message limit must be positive.")
        options = ExportOptions(
            output=output_dir,
            include_media=bool(allowed),
            include_stickers="sticker" in allowed,
            allowed_media=frozenset(allowed),
            max_file_size=self._size_bytes(payload.get("max_file_size_bytes")),
            max_total_size=self._size_bytes(payload.get("max_total_size_bytes")),
            limit=limit_value or None,
            since=since,
            until=until,
            timezone=timezone_name,
            include_created=bool(payload.get("include_created", True)),
            join_invite=bool(payload.get("join", False)),
            quiet=True,
            page_size=100,
        )
        job = Job(
            id=uuid.uuid4().hex[:12],
            target=target,
            title=str(payload.get("title") or target),
            output_dir=output_dir,
        )
        job.add_event("Starting export...")
        with self._lock:
            self.jobs[job.id] = job
            self.active_job_id = job.id
        job.task = asyncio.create_task(self._run_export(job, options))
        return job.snapshot()

    @staticmethod
    def _size_bytes(value: Any) -> int | None:
        number = _safe_int(value, 0)
        return number if number > 0 else None

    @staticmethod
    def _parse_date(value: Any, zone: ZoneInfo, *, end_of_day: bool) -> Any:
        if not value:
            return None
        text = str(value).strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                from datetime import datetime, time

                day = datetime.strptime(text, "%Y-%m-%d").date()
                return datetime.combine(
                    day,
                    time(23, 59, 59) if end_of_day else time(0, 0, 0),
                    tzinfo=zone,
                )
            from datetime import datetime

            moment = datetime.fromisoformat(text)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=zone)
            return moment
        except ValueError as exc:
            raise ValueError(f"Invalid date: {text}") from exc

    async def _run_export(self, job: Job, options: ExportOptions) -> None:
        client = self._require_client()
        job.status = "running"
        try:
            exporter = Exporter(
                client,
                options,
                message_callback=job.add_event,
                progress_factory=lambda total_files, total_bytes: WebProgress(
                    job, total_files, total_bytes
                ),
            )
            stats = await exporter.export(job.target)
            job.stats = _jsonable(asdict(stats))
            job.status = "completed"
            job.phase = "done"
            job.view_url = f"/view/{job.id}/messages.html"
            job.message = "Export completed."
            job.add_event(
                f"Completed: {stats.messages} messages, "
                f"{stats.photos + stats.videos + stats.documents + stats.stickers} media items."
            )
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.phase = "cancelled"
            job.message = "Export cancelled."
            job.add_event("Export cancelled.")
        except Exception as exc:  # noqa: BLE001 - report failures to the browser
            job.status = "failed"
            job.phase = "failed"
            job.error = str(exc)
            job.message = f"Export failed: {exc}"
            job.add_event(f"Error: {exc}")
        finally:
            job.finished_at = time.time()
            job.task = None
            with self._lock:
                if self.active_job_id == job.id:
                    self.active_job_id = None

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def active_job(self) -> dict[str, Any] | None:
        with self._lock:
            if self.active_job_id is None:
                return None
            job = self.jobs.get(self.active_job_id)
            return job.snapshot() if job is not None else None

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("Export job not found.")
        if job.status != "running" or job.task is None:
            return job.snapshot()
        job.cancel_requested = True
        job.add_event("Cancelling export...")
        job.task.cancel()
        return job.snapshot()

    # -- logging -----------------------------------------------------------

    def log(self, text: str) -> None:
        if self.quiet:
            return
        with self._print_lock:
            print(text, file=sys.stderr, flush=True)


class GuiHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.runtime: Runtime
        self.quiet = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"EitaaExport/{__version__}"

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined,no-any-return]

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        print(f"[gui] {self.address_string()} {fmt % args}", file=sys.stderr)

    # -- plumbing ----------------------------------------------------------

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "File not found"}, 404)
            return
        content_type, _ = mimetypes.guess_type(path.name)
        if path.suffix in {".html", ".css", ".js", ".json", ".txt"}:
            content_type = (content_type or "text/plain") + "; charset=utf-8"
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with path.open("rb") as handle:
                shutil.copyfileobj(handle, self.wfile, 256 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length > 2 * 1024 * 1024:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise TypeError("Expected a JSON object")
        return value

    def _exception(self, exc: BaseException) -> None:
        if isinstance(exc, AuthenticationRequired):
            self._send_json({"error": "Not signed in."}, 401)
        elif isinstance(exc, OtpError):
            self._send_json(
                {
                    "error": f"Login failed: {getattr(exc.reason, 'value', exc.reason)}",
                    "retry_after_seconds": exc.retry_after_seconds,
                },
                400,
            )
        elif isinstance(exc, ActiveExportError):
            self._send_json({"error": str(exc)}, 409)
        elif isinstance(exc, PeerResolutionError):
            self._send_json({"error": f"Peer not found: {exc}"}, 404)
        elif isinstance(exc, EitaaRPCError):
            if exc.code == 401:
                self._send_json({"error": "Not signed in."}, 401)
                return
            self._send_json(
                {
                    "error": f"Eitaa server error: {exc.code} {exc.text}".strip(),
                    "retry_after_seconds": exc.retry_after_seconds,
                },
                502,
            )
        elif isinstance(exc, (TypeError, ValueError, TimeoutError)):
            self._send_json({"error": str(exc)}, 400)
        elif isinstance(exc, EitaaError):
            self._send_json({"error": str(exc)}, 502)
        else:
            self._send_json({"error": f"Unexpected error: {exc}"}, 500)

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path in {"/", "/index.html"}:
                self._send_file(_WEB_ROOT / "index.html")
                return
            if path == "/favicon.ico":
                self._send_bytes(b"", "image/x-icon", status=204)
                return
            if path.startswith("/static/"):
                relative = path[len("/static/") :]
                candidate = (_WEB_ROOT / relative).resolve()
                if not candidate.is_relative_to(_WEB_ROOT.resolve()):
                    self._send_json({"error": "Not found"}, 404)
                    return
                self._send_file(candidate)
                return
            if path == "/api/status":
                self._send_json(self.runtime.call(self.runtime.status(), timeout=60))
                return
            if path == "/api/chats":
                query = parse_qs(parsed.query)
                limit = _safe_int((query.get("limit") or ["100"])[0], 100)
                search = (query.get("query") or [""])[0]
                rows = self.runtime.call(
                    self.runtime.chats(limit=limit, query=search),
                    timeout=120,
                )
                self._send_json({"chats": rows})
                return
            if path == "/api/jobs/active":
                self._send_json({"job": self.runtime.active_job()})
                return
            job_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)", path)
            if job_match:
                job = self.runtime.get_job(job_match.group(1))
                if job is None:
                    self._send_json({"error": "Export job not found."}, 404)
                else:
                    self._send_json(job.snapshot())
                return
            view_match = re.fullmatch(r"/view/([A-Za-z0-9_-]+)/(.*)", path)
            if view_match:
                job = self.runtime.get_job(view_match.group(1))
                if job is None:
                    self._send_json({"error": "Export not found."}, 404)
                    return
                relative = view_match.group(2) or "messages.html"
                root = job.output_dir.resolve()
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    self._send_json({"error": "Not found."}, 404)
                    return
                if target.is_dir():
                    target = target / "messages.html"
                self._send_file(target)
                return
            self._send_json({"error": "Not found."}, 404)
        except BaseException as exc:  # noqa: BLE001 - map every failure to JSON
            self._exception(exc)

    # -- POST --------------------------------------------------------------

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            payload = self._read_json()
            if path == "/api/auth/request":
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    raise ValueError("Phone number is required.")
                self._send_json(self.runtime.call(self.runtime.request_code(phone), timeout=90))
                return
            if path == "/api/auth/resend":
                phone = str(payload.get("phone") or "").strip()
                code_hash = str(payload.get("phone_code_hash") or "").strip()
                if not phone or not code_hash:
                    raise ValueError("Phone number and code hash are required.")
                self._send_json(
                    self.runtime.call(
                        self.runtime.resend_code(phone, code_hash),
                        timeout=90,
                    )
                )
                return
            if path == "/api/auth/verify":
                phone = str(payload.get("phone") or "").strip()
                code_hash = str(payload.get("phone_code_hash") or "").strip()
                code = str(payload.get("code") or "").strip()
                if not phone or not code_hash or not code:
                    raise ValueError("Phone number, code hash, and code are required.")
                self._send_json(
                    self.runtime.call(
                        self.runtime.verify_code(phone, code_hash, code),
                        timeout=120,
                    )
                )
                return
            if path == "/api/auth/logout":
                self._send_json(self.runtime.call(self.runtime.logout(), timeout=60))
                return
            if path == "/api/resolve":
                target = str(payload.get("target") or "").strip()
                self._send_json(
                    {"target": self.runtime.call(self.runtime.resolve(target), timeout=90)}
                )
                return
            if path == "/api/export":
                self._send_json(self.runtime.call(self.runtime.start_export(payload), timeout=30))
                return
            cancel_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)/cancel", path)
            if cancel_match:
                self._send_json(
                    self.runtime.call(
                        self.runtime.cancel_job(cancel_match.group(1)),
                        timeout=30,
                    )
                )
                return
            self._send_json({"error": "Not found."}, 404)
        except BaseException as exc:  # noqa: BLE001
            self._exception(exc)


def _find_server(host: str, port: int) -> GuiHTTPServer:
    return GuiHTTPServer((host, port), Handler)


def serve_gui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    profile: str | None = None,
    open_browser: bool = True,
    quiet: bool = False,
) -> int:
    root = output_root.expanduser() if output_root else Path.home() / "EitaaExports"
    root.mkdir(parents=True, exist_ok=True)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "Warning: the GUI API is not protected by a password. "
            "Only expose it on a trusted network.",
            file=sys.stderr,
        )
    runtime = Runtime(root, profile=profile, quiet=quiet)
    try:
        runtime.start()
    except Exception as exc:  # noqa: BLE001 - report startup failures cleanly
        runtime.stop()
        print(f"Could not initialise the Eitaa client: {exc}", file=sys.stderr)
        return 1
    try:
        server = _find_server(host, port)
    except OSError as exc:
        runtime.stop()
        print(f"Could not start the GUI server on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    server.runtime = runtime
    server.quiet = quiet
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host!s}:{actual_port}"
    print(f"Eitaa Export GUI: {url}", flush=True)
    print(f"Export folder:   {root.resolve()}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nStopping GUI...")
    finally:
        server.server_close()
        runtime.stop()
    return 0


__all__ = ["Handler", "Job", "Runtime", "WebProgress", "serve_gui"]
