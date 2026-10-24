"""Attachment planning, naming, and downloading."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from eitaa_cli.errors import EitaaRPCError

from .models import MediaInfo
from .progress import ProgressReporter
from .render import format_file_stamp

_BAD_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_IMAGE_SIZES = {"photoSize", "photoCachedSize", "photoSizeProgressive"}
_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/x-tgsticker": ".tgs",
    "application/pdf": ".pdf",
}


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


@dataclass(slots=True)
class MediaJob:
    info: MediaInfo
    location: dict[str, Any] | None = None
    size: int = 0
    thumb_location: dict[str, Any] | None = None
    thumb_size: int = 0
    dest: Path | None = None
    thumb_dest: Path | None = None
    inline: bytes | None = None
    thumb_inline: bytes | None = None
    same_thumb: bool = False
    fallback_thumb: bool = False


class MediaPlanner:
    """Turn raw ``MessageMedia`` objects into concrete download jobs."""

    def __init__(self, out_dir: Path, tz: tzinfo, *, include_media: bool = True) -> None:
        self.out_dir = out_dir
        self.tz = tz
        self.include_media = include_media
        self.counters: dict[str, int] = defaultdict(int)
        self.used: set[str] = set()

    def plan(
        self,
        raw_media: Any,
        message_id: int,
        timestamp: int,
        *,
        emoji_map: dict[int, str] | None = None,
    ) -> MediaJob | None:
        media = _obj(raw_media)
        if not media:
            return None
        predicate = _str(media.get("_"))
        if predicate == "messageMediaPhoto":
            return self._plan_photo(_obj(media.get("photo")), timestamp)
        if predicate == "messageMediaDocument":
            return self._plan_document(_obj(media.get("document")), timestamp)
        if predicate in {"messageMediaGeo", "messageMediaGeoLive", "messageMediaVenue"}:
            return self._plan_location(media)
        if predicate == "messageMediaContact":
            name = " ".join(
                part
                for part in (
                    _str(media.get("first_name")),
                    _str(media.get("last_name")),
                )
                if part
            ).strip()
            return MediaJob(
                MediaInfo(
                    kind="contact",
                    title=name or "Contact",
                    description=_str(media.get("phone_number")),
                )
            )
        if predicate == "messageMediaPoll":
            return self._plan_poll(media)
        if predicate == "messageMediaDice":
            emoji = _str(media.get("emoticon"), "🎲")
            value = _int(media.get("value"))
            return MediaJob(
                MediaInfo(kind="game", title=emoji, description=str(value) if value else "")
            )
        if predicate == "messageMediaGame":
            game = _obj(media.get("game"))
            return MediaJob(
                MediaInfo(
                    kind="game",
                    title=_str(game.get("title"), "Game"),
                    description=_str(game.get("description")),
                )
            )
        if predicate == "messageMediaInvoice":
            return MediaJob(
                MediaInfo(
                    kind="invoice",
                    title=_str(media.get("title"), "Invoice"),
                    description=_str(media.get("description")),
                    mime="invoice",
                )
            )
        if predicate == "messageMediaWebPage":
            return None
        return None

    def _stamp(self, timestamp: int) -> str:
        return format_file_stamp(datetime.fromtimestamp(timestamp, self.tz))

    def _counter(self, kind: str) -> int:
        self.counters[kind] += 1
        return self.counters[kind]

    def _unique(self, path: str) -> str:
        if path not in self.used:
            self.used.add(path)
            return path
        stem, dot, suffix = path.rpartition(".")
        if not dot:
            stem, suffix = path, ""
        index = 1
        while True:
            candidate = f"{stem} ({index}){('.' + suffix) if suffix else ''}"
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate
            index += 1

    # ----- photos ---------------------------------------------------------

    def _plan_photo(self, photo: dict[str, Any], timestamp: int) -> MediaJob | None:
        if not photo or _str(photo.get("_")) == "photoEmpty":
            return None
        sizes = [
            size
            for size in _list(photo.get("sizes"))
            if isinstance(size, dict) and _str(size.get("_")) in _IMAGE_SIZES
        ]
        sizes = [size for size in sizes if self._side(size) > 0 or self._inline_bytes(size)]
        if not sizes:
            return None
        largest = max(sizes, key=lambda item: (self._side(item), self._size_bytes(item)))
        thumb = self._choose_thumb(sizes, largest)
        stamp = self._stamp(timestamp)
        number = self._counter("photo")
        rel = self._unique(f"photos/photo_{number}@{stamp}.jpg")
        rel_thumb = rel[:-4] + "_thumb.jpg" if rel.endswith(".jpg") else rel + "_thumb.jpg"
        self.used.add(rel_thumb)

        info = MediaInfo(
            kind="photo",
            path=rel,
            thumb=rel_thumb,
            width=_int(largest.get("w")),
            height=_int(largest.get("h")),
            size=self._size_bytes(largest),
        )
        job = MediaJob(
            info=info,
            location=self._photo_location(photo, _str(largest.get("type"))),
            size=self._size_bytes(largest),
            dest=self.out_dir / rel,
            thumb_dest=self.out_dir / rel_thumb,
            inline=self._inline_bytes(largest),
            same_thumb=thumb is largest,
            thumb_location=self._photo_location(photo, _str(thumb.get("type"))),
            thumb_size=self._size_bytes(thumb),
            thumb_inline=self._inline_bytes(thumb),
        )
        return job

    @staticmethod
    def _side(size: dict[str, Any]) -> int:
        return max(_int(size.get("w")), _int(size.get("h")))

    @staticmethod
    def _size_bytes(size: dict[str, Any]) -> int:
        predicate = _str(size.get("_"))
        if predicate == "photoCachedSize":
            data = size.get("bytes")
            return len(data) if isinstance(data, (bytes, bytearray)) else 0
        if predicate == "photoSizeProgressive":
            values = [value for value in _list(size.get("sizes")) if isinstance(value, int)]
            return max(values) if values else 0
        return _int(size.get("size"))

    @staticmethod
    def _inline_bytes(size: dict[str, Any]) -> bytes | None:
        data = size.get("bytes")
        return bytes(data) if isinstance(data, (bytes, bytearray)) else None

    def _choose_thumb(self, sizes: list[dict[str, Any]], largest: dict[str, Any]) -> dict[str, Any]:
        candidates = [size for size in sizes if size is not largest]
        if not candidates:
            return largest
        fitting = [size for size in candidates if self._side(size) <= 320]
        if fitting:
            return max(fitting, key=lambda item: self._side(item))
        return min(candidates, key=lambda item: self._side(item))

    @staticmethod
    def _photo_location(photo: dict[str, Any], size_type: str) -> dict[str, Any] | None:
        if not size_type:
            return None
        return {
            "_": "inputPhotoFileLocation",
            "id": _int(photo.get("id")),
            "access_hash": _int(photo.get("access_hash")),
            "file_reference": photo.get("file_reference") or b"",
            "thumb_size": size_type,
        }

    # ----- documents ------------------------------------------------------

    def _plan_document(self, document: dict[str, Any], timestamp: int) -> MediaJob | None:
        if not document or _str(document.get("_")) == "documentEmpty":
            return None
        attributes = _list(document.get("attributes"))
        filename = ""
        title = ""
        performer = ""
        duration = 0
        width = 0
        height = 0
        is_sticker = False
        is_video = False
        is_round = False
        is_animated = False
        is_audio = False
        is_voice = False
        sticker_emoji = ""

        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            predicate = _str(attribute.get("_"))
            if predicate == "documentAttributeFilename":
                filename = _str(attribute.get("file_name"))
            elif predicate == "documentAttributeVideo":
                is_video = True
                is_round = bool(attribute.get("round_message"))
                duration = max(duration, _int(attribute.get("duration")))
                width = max(width, _int(attribute.get("w")))
                height = max(height, _int(attribute.get("h")))
            elif predicate == "documentAttributeAudio":
                is_audio = True
                is_voice = bool(attribute.get("voice"))
                duration = max(duration, _int(attribute.get("duration")))
                title = _str(attribute.get("title"))
                performer = _str(attribute.get("performer"))
            elif predicate == "documentAttributeSticker":
                is_sticker = True
                sticker_emoji = _str(attribute.get("alt"))
            elif predicate == "documentAttributeAnimated":
                is_animated = True

        mime = _str(document.get("mime_type"))
        if is_sticker:
            kind = "sticker"
        elif is_video and is_round:
            kind = "round"
        elif is_animated and mime.startswith("video/") and not is_round:
            kind = "gif"
        elif is_video:
            kind = "video"
        elif is_audio and is_voice:
            kind = "voice"
        elif is_audio:
            kind = "audio"
        else:
            kind = "document"

        extension = self._extension(mime, filename, kind)
        clean_name = self._sanitize(filename)

        if kind == "sticker":
            directory = "stickers"
            if clean_name:
                base = clean_name
            elif extension == ".tgs":
                base = "AnimatedSticker" + extension
            else:
                base = f"sticker_{self._counter('sticker')}{extension or '.webp'}"
            if not Path(base).suffix:
                base += extension or ".webp"
            display_title = base
        elif kind in {"video", "gif"}:
            directory = "video_files"
            if clean_name:
                base = clean_name
            else:
                prefix = "animation" if kind == "gif" else "video"
                base = f"{prefix}_{self._counter(kind)}@{self._stamp(timestamp)}{extension}"
            if not Path(base).suffix:
                base += extension or ".mp4"
            display_title = "GIF" if kind == "gif" else "Video file"
        elif kind == "round":
            directory = "round_video_messages"
            base = clean_name or f"round_{self._counter('round')}@{self._stamp(timestamp)}.mp4"
            if not Path(base).suffix:
                base += ".mp4"
            display_title = "Video message"
        elif kind == "voice":
            directory = "voice_messages"
            base = clean_name or f"voice_{self._counter('voice')}@{self._stamp(timestamp)}.ogg"
            if not Path(base).suffix:
                base += ".ogg"
            display_title = "Voice message"
        elif kind == "audio":
            directory = "files"
            base = clean_name or f"audio_{self._counter('audio')}@{self._stamp(timestamp)}{extension}"
            if not Path(base).suffix:
                base += extension or ".mp3"
            display_title = title or clean_name or "Audio file"
        else:
            directory = "files"
            base = clean_name or f"file_{self._counter('document')}@{self._stamp(timestamp)}{extension}"
            if not Path(base).suffix:
                base += extension or ".bin"
            display_title = clean_name or "File"

        rel = self._unique(f"{directory}/{base}")
        info = MediaInfo(
            kind=kind,
            path=rel,
            width=width,
            height=height,
            duration=duration,
            size=_int(document.get("size")),
            title=display_title,
            description=performer,
            mime=mime,
            sticker_emoji=sticker_emoji,
        )
        job = MediaJob(
            info=info,
            location=self._document_location(document, ""),
            size=_int(document.get("size")),
            dest=self.out_dir / rel,
        )

        if kind in {"video", "gif", "round"}:
            thumb = self._choose_document_thumb(document)
            thumb_name = rel + "_thumb.jpg"
            info.thumb = thumb_name
            job.thumb_dest = self.out_dir / thumb_name
            if thumb is not None:
                job.thumb_location = self._document_location(document, _str(thumb.get("type")))
                job.thumb_size = self._size_bytes(thumb)
                job.thumb_inline = self._inline_bytes(thumb)
            else:
                job.fallback_thumb = True
        return job

    def _choose_document_thumb(self, document: dict[str, Any]) -> dict[str, Any] | None:
        thumbs = [
            thumb
            for thumb in _list(document.get("thumbs"))
            if isinstance(thumb, dict) and _str(thumb.get("_")) in _IMAGE_SIZES
        ]
        if not thumbs:
            return None
        fitting = [thumb for thumb in thumbs if self._side(thumb) <= 320]
        if fitting:
            return max(fitting, key=lambda item: self._side(item))
        return max(thumbs, key=lambda item: self._side(item))

    @staticmethod
    def _document_location(document: dict[str, Any], thumb_size: str) -> dict[str, Any]:
        return {
            "_": "inputDocumentFileLocation",
            "id": _int(document.get("id")),
            "access_hash": _int(document.get("access_hash")),
            "file_reference": document.get("file_reference") or b"",
            "thumb_size": thumb_size,
        }

    @staticmethod
    def _extension(mime: str, filename: str, kind: str) -> str:
        if filename and "." in filename:
            extension = "." + filename.rsplit(".", 1)[-1]
            if len(extension) <= 10 and extension[1:].replace("-", "").isalnum():
                return extension.lower()
        if mime in _MIME_EXTENSIONS:
            return _MIME_EXTENSIONS[mime]
        guessed = mimetypes.guess_extension(mime or "") or ""
        if guessed:
            return guessed
        return {
            "sticker": ".webp",
            "video": ".mp4",
            "gif": ".mp4",
            "round": ".mp4",
            "voice": ".ogg",
            "audio": ".mp3",
        }.get(kind, ".bin")

    @staticmethod
    def _sanitize(name: str) -> str:
        if not name:
            return ""
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        name = _BAD_FILENAME_CHARS.sub("_", name).strip(" .")
        if name in {"", ".", ".."}:
            return ""
        if len(name) > 120:
            stem, dot, suffix = name.rpartition(".")
            if dot and len(suffix) <= 10:
                name = stem[: 120 - len(suffix) - 1] + "." + suffix
            else:
                name = name[:120]
        return name

    # ----- non-file media -------------------------------------------------

    def _plan_location(self, media: dict[str, Any]) -> MediaJob:
        geo = _obj(media.get("geo"))
        latitude = geo.get("lat")
        longitude = geo.get("long")
        description = ""
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            description = f"{latitude:.6f}, {longitude:.6f}"
        title = _str(media.get("title")) or "Location"
        if _str(media.get("_")) == "messageMediaVenue":
            address = _str(media.get("address"))
            if address:
                description = f"{address}\n{description}".strip()
        return MediaJob(
            MediaInfo(kind="location", title=title, description=description, mime="geo")
        )

    def _plan_poll(self, media: dict[str, Any]) -> MediaJob:
        poll = _obj(media.get("poll"))
        question = _str(poll.get("question"), "Poll")
        answers: list[str] = []
        for answer in _list(poll.get("answers")):
            if isinstance(answer, dict):
                text = _str(answer.get("text"))
                if text:
                    answers.append(text)
        return MediaJob(
            MediaInfo(kind="poll", title=question, description="\n".join(answers))
        )


class MediaDownloader:
    """Download planned media with resumable chunks and per-file progress bars."""

    _CHUNK_SIZE = 512 * 1024

    def __init__(
        self,
        client: Any,
        out_dir: Path,
        *,
        quiet: bool = False,
    ) -> None:
        self.client = client
        self.out_dir = out_dir
        self.quiet = quiet
        self.reporter: ProgressReporter | None = None
        self.warnings: list[str] = []
        self.bytes_written = 0

    async def download_all(self, jobs: Iterable[MediaJob]) -> None:
        jobs = [job for job in jobs if job.dest is not None]
        if not jobs:
            return
        total_bytes = sum(self._job_bytes(job) for job in jobs)
        self.reporter = ProgressReporter(
            quiet=self.quiet,
            total_files=len(jobs),
            total_bytes=total_bytes,
        )
        with self.reporter:
            for index, job in enumerate(jobs, start=1):
                await self._download_job(job, index)
                info = job.info
                if info.available and info.path:
                    self.bytes_written += self._file_size(self.out_dir / info.path)
                    if info.thumb:
                        self.bytes_written += self._file_size(self.out_dir / info.thumb)

    @staticmethod
    def _job_bytes(job: MediaJob) -> int:
        if job.inline is not None:
            full = max(1, len(job.inline))
        else:
            full = max(1, job.size)
        total = full
        if job.thumb_dest is not None:
            if job.same_thumb:
                total += full
            elif job.thumb_inline is not None:
                total += max(1, len(job.thumb_inline))
            elif job.thumb_size > 0:
                total += job.thumb_size
        return total

    async def _download_job(self, job: MediaJob, index: int) -> None:
        info = job.info
        try:
            await self._download_full(job, index)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one broken file must not abort the export
            info.available = False
            self.warnings.append(f"{info.path or info.title}: {exc}")
            if info.kind == "photo":
                info.path = None
                info.thumb = None
            return
        try:
            await self._download_thumb(job, index)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a thumbnail is optional
            self.warnings.append(f"thumbnail for {info.path or info.title}: {exc}")
        if info.kind == "photo":
            await self._ensure_photo_thumb(job)
        elif info.kind in {"video", "gif", "round"}:
            await self._ensure_video_thumb(job)

    def _reporter(self) -> ProgressReporter | None:
        return self.reporter

    async def _download_full(self, job: MediaJob, index: int) -> None:
        info = job.info
        label = info.path or info.title or info.kind
        expected = job.size
        reporter = self._reporter()
        if not job.dest or not info.path:
            return

        if job.inline is not None:
            if reporter is not None:
                reporter.begin_file(index, label, len(job.inline))
            try:
                self._write_bytes(job.dest, job.inline)
            except Exception:
                if reporter is not None:
                    reporter.finish(success=False)
                raise
            if reporter is not None:
                reporter.finish()
            return

        if expected > 0 and self._is_complete(job.dest, expected):
            if reporter is not None:
                reporter.begin_file(index, label, expected)
                reporter.finish(status="cached")
            return

        if job.location is None:
            if reporter is not None:
                reporter.begin_file(index, label, expected)
                reporter.finish(success=False)
            raise RuntimeError("attachment has no downloadable location")

        if reporter is not None:
            reporter.begin_file(index, label, expected)
        try:
            await self._download_stream(job.location, expected, job.dest)
        except Exception:
            if reporter is not None:
                reporter.finish(success=False)
            raise
        if reporter is not None:
            reporter.finish()

    async def _download_thumb(self, job: MediaJob, index: int) -> None:
        dest = job.thumb_dest
        if dest is None:
            return
        info = job.info
        label = f"{info.path or info.title or info.kind} (thumbnail)"
        reporter = self._reporter()

        if job.same_thumb:
            source = job.dest
            if source is None or not source.exists():
                return
            size = max(1, self._file_size(source))
            if reporter is not None:
                reporter.begin_file(index, label, size)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copyfile, source, dest)
            except Exception:
                if reporter is not None:
                    reporter.finish(success=False)
                raise
            if reporter is not None:
                reporter.finish(status="cached")
            return

        if job.thumb_inline is not None:
            if reporter is not None:
                reporter.begin_file(index, label, len(job.thumb_inline))
            try:
                self._write_bytes(dest, job.thumb_inline)
            except Exception:
                if reporter is not None:
                    reporter.finish(success=False)
                raise
            if reporter is not None:
                reporter.finish()
            return

        if job.thumb_location is None or job.thumb_size <= 0:
            return
        if self._is_complete(dest, job.thumb_size):
            if reporter is not None:
                reporter.begin_file(index, label, job.thumb_size)
                reporter.finish(status="cached")
            return
        if reporter is not None:
            reporter.begin_file(index, label, job.thumb_size)
        try:
            await self._download_stream(job.thumb_location, job.thumb_size, dest)
        except Exception:
            if reporter is not None:
                reporter.finish(success=False)
            raise
        if reporter is not None:
            reporter.finish()

    async def _download_stream(
        self,
        location: dict[str, Any],
        size: int,
        output: Path,
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        part = output.with_name(output.name + ".part")
        try:
            await asyncio.to_thread(part.unlink)
        except FileNotFoundError:
            pass
        offset = 0
        try:
            with part.open("wb") as handle:
                while size <= 0 or offset < size:
                    limit = self._CHUNK_SIZE if size <= 0 else min(self._CHUNK_SIZE, size - offset)
                    chunk = await self._fetch_chunk(location, offset, limit)
                    if not chunk:
                        if size > 0 and offset < size:
                            raise RuntimeError(
                                f"download stopped at {offset} of {size} bytes"
                            )
                        break
                    await asyncio.to_thread(handle.write, chunk)
                    offset += len(chunk)
                    if self.reporter is not None:
                        self.reporter.advance(len(chunk))
                    if size <= 0 and len(chunk) < limit:
                        break
            if size > 0 and offset < size:
                raise RuntimeError(f"download stopped at {offset} of {size} bytes")
            await asyncio.to_thread(os.replace, part, output)
        except BaseException:
            try:
                part.unlink()
            except OSError:
                pass
            raise

    async def _fetch_chunk(
        self,
        location: dict[str, Any],
        offset: int,
        limit: int,
    ) -> bytes:
        params = {
            "location": location,
            "offset": offset,
            "limit": limit,
            "cdn_supported": False,
        }
        for attempt in range(3):
            try:
                response = await self.client.invoke(
                    "upload.getFile",
                    params,
                    kind="download",
                )
            except EitaaRPCError as exc:
                wait = exc.retry_after_seconds
                if wait is None or attempt >= 2:
                    raise
                wait = min(int(wait), 120)
                if self.reporter is not None:
                    self.reporter.note(f"Rate limited; retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            if not isinstance(response, dict) or response.get("_") != "upload.file":
                raise RuntimeError("unexpected response from upload.getFile")
            data = response.get("bytes")
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if data is None:
                return b""
            raise RuntimeError("upload.getFile returned non-bytes data")
        raise RuntimeError("download retry loop exhausted")

    def _write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def _is_complete(path: Path, expected: int) -> bool:
        try:
            return path.is_file() and path.stat().st_size >= expected > 0
        except OSError:
            return False

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return path.stat().st_size if path.is_file() else 0
        except OSError:
            return 0

    async def _ensure_photo_thumb(self, job: MediaJob) -> None:
        if not job.dest or not job.thumb_dest or job.thumb_dest.exists():
            return
        if not job.dest.exists():
            return
        await asyncio.to_thread(
            _resize_image,
            job.dest,
            job.thumb_dest,
            320,
        )

    async def _ensure_video_thumb(self, job: MediaJob) -> None:
        if not job.thumb_dest or job.thumb_dest.exists():
            return
        info = job.info
        width, height = info.width or 16, info.height or 9
        await asyncio.to_thread(
            _draw_video_placeholder,
            job.thumb_dest,
            width,
            height,
        )


def _resize_image(source: Path, destination: Path, max_side: int) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=86)
    except Exception:  # noqa: BLE001 - thumbnails are best-effort
        return


def _draw_video_placeholder(destination: Path, width: int, height: int) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    try:
        scale = min(260 / max(width, 1), 260 / max(height, 1), 1.0)
        canvas_width = max(1, round(width * scale))
        canvas_height = max(1, round(height * scale))
        image = Image.new("RGB", (canvas_width, canvas_height), (38, 45, 52))
        draw = ImageDraw.Draw(image)
        radius = max(12, min(canvas_width, canvas_height) // 7)
        center = (canvas_width // 2, canvas_height // 2)
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            fill=(255, 255, 255),
        )
        triangle = [
            (center[0] - radius // 3, center[1] - radius // 2),
            (center[0] - radius // 3, center[1] + radius // 2),
            (center[0] + radius // 2, center[1]),
        ]
        draw.polygon(triangle, fill=(38, 45, 52))
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "JPEG", quality=88)
    except Exception:  # noqa: BLE001
        return


__all__ = ["MediaDownloader", "MediaJob", "MediaPlanner"]
