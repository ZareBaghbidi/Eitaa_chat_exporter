"""Telegram-Desktop-compatible HTML rendering."""

from __future__ import annotations

import html
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, tzinfo

from .models import ChatInfo, MediaInfo, Message

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _esc(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def _attr(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def offset_label(moment: datetime) -> str:
    offset = moment.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"UTC{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def format_day(moment: datetime) -> str:
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def format_clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def format_stamp(moment: datetime) -> str:
    return moment.strftime("%d.%m.%Y %H:%M:%S")


def format_title(moment: datetime) -> str:
    return f"{format_stamp(moment)} {offset_label(moment)}"


def format_file_stamp(moment: datetime) -> str:
    return moment.strftime("%d-%m-%Y_%H-%M-%S")


def format_size(size: int) -> str:
    if size <= 0:
        return ""
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return f"{size} B"


def format_duration(seconds: int, *, pad: bool = True) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    if pad:
        return f"{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fit_dimensions(width: int, height: int, max_side: int, fallback: tuple[int, int]) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return fallback
    scale = min(1.0, max_side / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _mime_label(mime: str, file_name: str) -> str:
    lower = (mime or "").lower()
    extension = file_name.rsplit(".", 1)[-1].upper() if "." in file_name else ""
    if lower == "application/pdf":
        return "PDF document"
    if lower in {"application/zip", "application/x-zip-compressed"}:
        return "ZIP archive"
    if lower in {"application/x-rar-compressed", "application/vnd.rar"}:
        return "RAR archive"
    if lower.startswith("text/"):
        return "Text document"
    if lower.startswith("image/"):
        return "Image"
    if lower.startswith("audio/"):
        return "Audio file"
    if lower.startswith("video/"):
        return "Video file"
    if lower == "application/vnd.android.package-archive":
        return "Android package"
    if extension:
        return f"{extension} file"
    return "File"


class Renderer:
    """Render normalized messages as a Telegram-style HTML page."""

    def __init__(
        self,
        chat: ChatInfo,
        messages: Iterable[Message],
        *,
        tz: tzinfo = UTC,
    ) -> None:
        self.chat = chat
        self.messages = list(messages)
        self.tz = tz

    def local(self, timestamp: int) -> datetime:
        return datetime.fromtimestamp(timestamp, self.tz)

    def render(self) -> str:
        history = self._render_history()
        return (
            "<!DOCTYPE html>\n"
            "<html>\n"
            "\n"
            " <head>\n"
            "\n"
            '  <meta charset="utf-8"/>\n'
            "<title>Exported Data</title>\n"
            '  <meta content="width=device-width, initial-scale=1.0" name="viewport"/>\n'
            "\n"
            '  <link href="css/style.css" rel="stylesheet"/>\n'
            "\n"
            '  <script src="js/script.js" type="text/javascript">\n'
            "\n"
            "  </script>\n"
            "\n"
            " </head>\n"
            "\n"
            ' <body onload="CheckLocation();">\n'
            "\n"
            '  <div class="page_wrap">\n'
            "\n"
            '   <div class="page_header">\n'
            "\n"
            '    <div class="content">\n'
            "\n"
            '     <div class="text bold">\n'
            f"{_esc(self.chat.title)}\n"
            "     </div>\n"
            "\n"
            "    </div>\n"
            "\n"
            "   </div>\n"
            "\n"
            '   <div class="page_body chat_page">\n'
            "\n"
            '    <div class="history">\n'
            "\n"
            f"{history}"
            "\n"
            "    </div>\n"
            "\n"
            "   </div>\n"
            "\n"
            "  </div>\n"
            "\n"
            " </body>\n"
            "\n"
            "</html>\n"
        )

    def _render_history(self) -> str:
        ordered = sorted(self.messages, key=lambda item: (item.date, item.id))
        blocks: list[str] = []
        date_counter = 0
        current_day: datetime | None = None
        previous: Message | None = None

        for message in ordered:
            moment = self.local(message.date)
            if current_day is None or moment.date() != current_day.date():
                date_counter += 1
                current_day = moment
                blocks.append(self._date_separator(date_counter, moment))
            if message.is_service:
                blocks.append(self._service(message))
                previous = None
                continue
            joined = self._joined(previous, message, moment)
            blocks.append(self._message(message, moment, joined=joined))
            previous = message
        return "".join(blocks)

    def _date_separator(self, index: int, moment: datetime) -> str:
        return (
            f'     <div class="message service" id="message-{index}">\n'
            "\n"
            '      <div class="body details">\n'
            f"{_esc(format_day(moment))}\n"
            "      </div>\n"
            "\n"
            "     </div>\n"
        )

    def _service(self, message: Message) -> str:
        photo = ""
        media = message.media
        if media is not None and media.kind == "photo" and media.has_file:
            thumb = media.thumb or media.path
            photo = (
                '      <div class="userpic_wrap">\n'
                "\n"
                f'       <a class="userpic_link" href="{_attr(media.path)}">\n'
                "\n"
                f'        <img class="userpic" src="{_attr(thumb)}" style="width: 60px; height: 60px"/>\n'
                "\n"
                "       </a>\n"
                "\n"
                "      </div>\n"
                "\n"
            )
        return (
            f'     <div class="message service" id="message{message.id}">\n'
            "\n"
            '      <div class="body details">\n'
            f"{_esc(message.action or '')}\n"
            "      </div>\n"
            "\n"
            f"{photo}"
            "     </div>\n"
        )

    def _joined(self, previous: Message | None, current: Message, moment: datetime) -> bool:
        if previous is None or previous.is_service:
            return False
        if current.forward is not None or current.reply_to_id is not None:
            return False
        if previous.sender_id != current.sender_id:
            return False
        if previous.signature != current.signature:
            return False
        delta = current.date - previous.date
        if delta < 0 or delta > 15 * 60:
            return False
        return self.local(previous.date).date() == moment.date()

    def _userpic(self, title: str, peer_id: int | None, url: str | None, *, extra_class: str = "") -> str:
        color = (abs(peer_id or 0) % 7) + 1
        initial = "?"
        for char in title.strip():
            if not char.isspace():
                initial = char.upper()
                break
        block = (
            f'<div class="userpic userpic{color}" style="width: 42px; height: 42px">\n'
            "\n"
            f'<div class="initials" style="line-height: 42px">\n'
            f"{_esc(initial)}\n"
            "</div>\n"
            "\n"
            "</div>\n"
        )
        if url:
            block = (
                f'<a class="userpic_link" href="{_attr(url)}">\n'
                f"{block}"
                "</a>\n"
            )
        classes = "pull_left userpic_wrap" + (f" {extra_class}" if extra_class else "")
        return (
            f'<div class="{classes}">\n'
            "\n"
            f"{block}"
            "\n"
            "</div>\n"
        )

    def _message(self, message: Message, moment: datetime, *, joined: bool) -> str:
        classes = "message default clearfix" + (" joined" if joined else "")
        parts: list[str] = [
            f'     <div class="{classes}" id="message{message.id}">\n',
            "\n",
        ]
        if not joined:
            parts.append(self._userpic(message.sender_name, message.sender_id, message.sender_url))
            parts.append("\n")

        parts.append('      <div class="body">\n')
        parts.append("\n")
        parts.append(
            f'       <div class="pull_right date details" title="{_attr(format_title(moment))}">\n'
            f"{_esc(format_clock(moment))}\n"
            "       </div>\n"
        )
        parts.append("\n")

        if not joined:
            parts.append('       <div class="from_name">\n')
            if message.sender_url:
                parts.append(
                    f'<a class="peer_link" href="{_attr(message.sender_url)}">'
                    f"{_esc(message.sender_name)}</a>\n"
                )
            else:
                parts.append(f"{_esc(message.sender_name)}\n")
            parts.append("       </div>\n")
            parts.append("\n")

        if message.reply_to_id is not None:
            if message.reply_other_chat:
                reply = "In reply to a message in another chat"
            else:
                reply = (
                    f'In reply to <a href="#go_to_message{message.reply_to_id}" '
                    f'onclick="return GoToMessage({message.reply_to_id})">this message</a>'
                )
            parts.append('       <div class="reply_to details">\n')
            parts.append(f"{reply}\n")
            parts.append("       </div>\n")
            parts.append("\n")

        if message.forward is not None:
            parts.append(self._forward(message, moment))
            parts.append("\n")
        else:
            if message.media is not None:
                media_html = self._media(message.media)
                if media_html:
                    parts.append(media_html)
                    parts.append("\n")

            if message.html_text:
                parts.append('       <div class="text">\n')
                parts.append(f"{message.html_text}\n")
                parts.append("       </div>\n")
                parts.append("\n")

        if message.signature:
            parts.append('       <div class="signature details">\n')
            parts.append(f"{_esc(message.signature)}\n")
            parts.append("       </div>\n")
            parts.append("\n")

        if message.reactions:
            parts.append(self._reactions(message.reactions))
            parts.append("\n")

        parts.append("      </div>\n")
        parts.append("\n")
        parts.append("     </div>\n")
        return "".join(parts)

    def _forward(self, message: Message, moment: datetime) -> str:
        forward = message.forward
        assert forward is not None
        source_date = self.local(forward.date) if forward.date else moment
        name = forward.name or "Forwarded message"
        inner: list[str] = [
            self._userpic(name, forward.peer_id, forward.url, extra_class="forwarded"),
            "\n",
            '<div class="forwarded body">\n',
            "\n",
            '       <div class="from_name">\n',
        ]
        if forward.url:
            inner.append(
                f'<a class="peer_link" href="{_attr(forward.url)}">{_esc(name)}</a> '
            )
        else:
            inner.append(f"{_esc(name)} ")
        inner.append(
            f'<span class="date details" title="{_attr(format_stamp(source_date))}"> '
            f"{_esc(format_stamp(source_date))}</span>\n"
        )
        inner.append("       </div>\n")
        inner.append("\n")

        if message.media is not None:
            media_html = self._media(message.media)
            if media_html:
                inner.append(media_html)
                inner.append("\n")
        if message.html_text:
            inner.append('       <div class="text">\n')
            inner.append(f"{message.html_text}\n")
            inner.append("       </div>\n")
            inner.append("\n")
        if forward.author:
            inner.append('       <div class="signature details">\n')
            inner.append(f"{_esc(forward.author)}\n")
            inner.append("       </div>\n")
            inner.append("\n")
        inner.append("</div>\n")
        return "".join(inner)

    def _reactions(self, reactions: list[tuple[str, int]]) -> str:
        parts = ['       <span class="reactions">\n', "\n"]
        for emoji, count in reactions:
            parts.append('        <span class="reaction">\n')
            parts.append(f'<span class="emoji">{_esc(emoji)}</span>\n')
            parts.append(f'<span class="count">{int(count)}</span>\n')
            parts.append("        </span>\n")
            parts.append("\n")
        parts.append("       </span>\n")
        return "".join(parts)

    def _media(self, media: MediaInfo) -> str:
        if media.kind == "photo":
            if not media.path:
                if media.note:
                    status = format_size(media.size) if media.size else ""
                    return self._info_media("media_photo", "Photo", media.note, status)
                return ""
            thumb = media.thumb or media.path
            width, height = fit_dimensions(media.width, media.height, 260, (260, 195))
            return (
                '<div class="media_wrap clearfix">\n'
                "\n"
                f'<a class="photo_wrap clearfix pull_left" href="{_attr(media.path)}">\n'
                "\n"
                f'<img class="photo" src="{_attr(thumb)}" style="width: {width}px; height: {height}px"/>\n'
                "\n"
                "</a>\n"
                "\n"
                "</div>\n"
            )

        if media.kind in {"video", "gif", "round"}:
            if not media.has_file:
                return self._media_fallback(media)
            poster = media.thumb
            width, height = fit_dimensions(media.width, media.height, 260, (260, 146))
            play = (
                '<div class="video_play_bg">\n'
                "\n"
                '<div class="gif_play">GIF</div>\n'
                "\n"
                "</div>\n"
                if media.kind == "gif"
                else '<div class="video_play_bg">\n'
                "\n"
                '<div class="video_play">\n'
                "\n"
                "</div>\n"
                "\n"
                "</div>\n"
            )
            duration = ""
            if media.duration:
                duration = (
                    '<div class="video_duration">\n'
                    f"{_esc(format_duration(media.duration))}\n"
                    "</div>\n"
                )
            image = ""
            if poster:
                image = (
                    f'<img class="video_file" src="{_attr(poster)}" '
                    f'style="width: {width}px; height: {height}px"/>\n'
                )
            return (
                '<div class="media_wrap clearfix">\n'
                "\n"
                f'<a class="video_file_wrap clearfix pull_left" href="{_attr(media.path)}">\n'
                "\n"
                f"{play}"
                "\n"
                f"{duration}"
                "\n"
                f"{image}"
                "\n"
                "</a>\n"
                "\n"
                "</div>\n"
            )

        if media.kind == "sticker":
            if not media.has_file:
                if media.note:
                    return self._info_media("media_file", "Sticker", media.note, "")
                return ""
            width, height = fit_dimensions(media.width, media.height, 128, (128, 128))
            return (
                '<div class="media_wrap clearfix">\n'
                "\n"
                f'<a class="sticker_wrap clearfix pull_left" href="{_attr(media.path)}">\n'
                "\n"
                f'<img class="sticker" src="{_attr(media.path)}" style="width: {width}px; height: {height}px"/>\n'
                "\n"
                "</a>\n"
                "\n"
                "</div>\n"
            )

        if media.kind in {"voice", "audio"}:
            return self._audio(media)

        if media.kind == "document":
            return self._document(media)

        if media.kind == "location":
            return self._info_media(
                "media_location",
                "Location",
                media.description,
                "",
            )

        if media.kind == "contact":
            return self._info_media(
                "media_contact",
                media.title or "Contact",
                media.description,
                "",
            )

        if media.kind == "poll":
            return self._poll(media)

        if media.kind == "game":
            return self._info_media("media_game", media.title or "Game", media.description, "")

        if media.kind == "invoice":
            return self._info_media(
                "media_invoice",
                media.title or "Invoice",
                media.description,
                "",
            )

        return ""

    def _media_fallback(self, media: MediaInfo) -> str:
        css = {
            "video": "media_video",
            "gif": "media_video",
            "round": "media_video",
            "voice": "media_voice_message",
            "audio": "media_audio_file",
        }.get(media.kind, "media_file")
        title = media.title or ("Video file" if css == "media_video" else "File")
        status_parts = []
        if media.duration:
            status_parts.append(format_duration(media.duration))
        if media.size:
            status_parts.append(format_size(media.size))
        status = ", ".join(status_parts)
        description = media.note or "Unavailable, please try again later."
        status_html = f'<div class="status details">{_esc(status)}</div>' if status else ""
        return (
            '<div class="media_wrap clearfix">\n'
            "\n"
            f'<div class="media clearfix pull_left {css}">\n'
            "\n"
            '<div class="fill pull_left"></div>\n'
            "\n"
            '<div class="body">\n'
            "\n"
            f'<div class="title bold">{_esc(title)}</div>\n'
            "\n"
            f'<div class="description">{_esc(description)}</div>\n'
            "\n"
            f"{status_html}\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
        )

    def _audio(self, media: MediaInfo) -> str:
        if not media.has_file:
            return self._media_fallback(media)
        css = "media_voice_message" if media.kind == "voice" else "media_audio_file"
        title = media.title or ("Voice message" if media.kind == "voice" else "Audio file")
        if media.path:
            title_html = f'<a href="{_attr(media.path)}">{_esc(title)}</a>'
        else:
            title_html = _esc(title)
        duration = format_duration(media.duration, pad=False) if media.duration else ""
        size = format_size(media.size)
        if media.kind == "voice":
            description = duration
            details = size
        else:
            description = media.description or duration
            details = ", ".join(part for part in (duration, size) if part)
        return (
            '<div class="media_wrap clearfix">\n'
            "\n"
            f'<div class="media clearfix pull_left {css}">\n'
            "\n"
            '<div class="fill pull_left"></div>\n'
            "\n"
            '<div class="body">\n'
            "\n"
            f'<div class="title bold">{title_html}</div>\n'
            "\n"
            f'<div class="description">{_esc(description)}</div>\n'
            "\n"
            f'<div class="status details">{_esc(details)}</div>\n'
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
        )

    def _document(self, media: MediaInfo) -> str:
        if not media.has_file:
            return self._media_fallback(media)
        title = media.title or "File"
        description = media.description or _mime_label(media.mime, title)
        size = format_size(media.size)
        return (
            '<div class="media_wrap clearfix">\n'
            "\n"
            f'<a class="media clearfix pull_left media_file" href="{_attr(media.path)}">\n'
            "\n"
            '<div class="fill pull_left"></div>\n'
            "\n"
            '<div class="body">\n'
            "\n"
            f'<div class="title bold">{_esc(title)}</div>\n'
            "\n"
            f'<div class="description">{_esc(description)}</div>\n'
            "\n"
            f'<div class="status details">{_esc(size)}</div>\n'
            "\n"
            "</div>\n"
            "\n"
            "</a>\n"
            "\n"
            "</div>\n"
        )

    def _info_media(self, css: str, title: str, description: str, status: str) -> str:
        status_html = f'<div class="status details">{_esc(status)}</div>' if status else ""
        return (
            '<div class="media_wrap clearfix">\n'
            "\n"
            f'<div class="media clearfix pull_left {css}">\n'
            "\n"
            '<div class="fill pull_left"></div>\n'
            "\n"
            '<div class="body">\n'
            "\n"
            f'<div class="title bold">{_esc(title)}</div>\n'
            "\n"
            f'<div class="description">{_esc(description)}</div>\n'
            "\n"
            f"{status_html}\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
        )

    def _poll(self, media: MediaInfo) -> str:
        lines = [line for line in media.description.split("\n") if line.strip()]
        answers = "".join(f'<div class="description">{_esc(line)}</div>\n' for line in lines)
        return (
            '<div class="media_wrap clearfix">\n'
            "\n"
            '<div class="media clearfix pull_left media_poll">\n'
            "\n"
            '<div class="body">\n'
            "\n"
            f'<div class="question">{_esc(media.title)}</div>\n'
            "\n"
            f"{answers}"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
            "\n"
            "</div>\n"
        )
