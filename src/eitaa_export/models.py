"""Normalized data structures used by the exporter and the HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass, field

EITAA_PROFILE_BASE = "https://eitaa.com"


def profile_url(username: str | None, peer_id: int | None) -> str | None:
    """Return a clickable Eitaa profile URL for a peer, when one can be built."""

    if username:
        return f"{EITAA_PROFILE_BASE}/{username.lstrip('@')}"
    if peer_id:
        return f"{EITAA_PROFILE_BASE}/{peer_id}"
    return None


@dataclass(slots=True)
class ChatInfo:
    """The conversation being exported."""

    kind: str  # private | group | supergroup | channel
    peer_id: int
    title: str
    username: str | None = None
    date: int = 0
    about: str = ""
    members: int = 0

    @property
    def is_broadcast(self) -> bool:
        return self.kind == "channel"

    @property
    def is_group(self) -> bool:
        return self.kind in {"group", "supergroup"}

    @property
    def url(self) -> str | None:
        return profile_url(self.username, self.peer_id)

    @property
    def initial(self) -> str:
        for char in self.title.strip():
            if not char.isspace():
                return char.upper()
        return "?"


@dataclass(slots=True)
class ForwardInfo:
    """Origin of a forwarded message."""

    name: str
    username: str | None = None
    peer_id: int | None = None
    date: int = 0
    author: str | None = None

    @property
    def url(self) -> str | None:
        return profile_url(self.username, self.peer_id)


@dataclass(slots=True)
class MediaInfo:
    """A single attachment, already downloaded (or deliberately not downloaded)."""

    kind: str  # photo | video | gif | round | voice | audio | document | sticker | location | contact | poll | game
    path: str | None = None  # path relative to the export root, POSIX style
    thumb: str | None = None
    width: int = 0
    height: int = 0
    duration: int = 0
    size: int = 0
    title: str = ""
    description: str = ""
    mime: str = ""
    sticker_emoji: str = ""
    available: bool = True

    @property
    def has_file(self) -> bool:
        return bool(self.path) and self.available


@dataclass(slots=True)
class Message:
    """A normalized message or service event."""

    id: int
    date: int
    sender_name: str = ""
    sender_username: str | None = None
    sender_id: int | None = None
    signature: str = ""
    html_text: str = ""
    plain_text: str = ""
    action: str | None = None
    reply_to_id: int | None = None
    reply_other_chat: bool = False
    forward: ForwardInfo | None = None
    media: MediaInfo | None = None
    reactions: list[tuple[str, int]] = field(default_factory=list)
    grouped_id: int | None = None
    out: bool = False
    pinned: bool = False

    @property
    def is_service(self) -> bool:
        return self.action is not None

    @property
    def sender_url(self) -> str | None:
        return profile_url(self.sender_username, self.sender_id)


@dataclass(slots=True)
class ExportStats:
    messages: int = 0
    service_messages: int = 0
    photos: int = 0
    videos: int = 0
    voice_messages: int = 0
    documents: int = 0
    stickers: int = 0
    other_media: int = 0
    bytes_written: int = 0
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""
