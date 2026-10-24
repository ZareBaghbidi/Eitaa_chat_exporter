"""Telegram-compatible rich-text rendering for Eitaa message entities."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from .models import EITAA_PROFILE_BASE


def _scalar(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def utf16_to_index(text: str, offset: int) -> int:
    """Translate a UTF-16 code-unit offset (as used by TL) into a str index."""

    if offset <= 0:
        return 0
    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


@dataclass(slots=True)
class Span:
    start: int
    end: int
    entity: dict[str, Any]


@dataclass(slots=True)
class _Node:
    start: int
    end: int
    entity: dict[str, Any] | None = None
    children: list[_Node] = field(default_factory=list)


def _spans(text: str, entities: list[dict[str, Any]] | None) -> list[Span]:
    result: list[Span] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        offset = entity.get("offset")
        length = entity.get("length")
        if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
            continue
        start = utf16_to_index(text, offset)
        end = utf16_to_index(text, offset + length)
        if end > start:
            result.append(Span(start, end, entity))
    return result


def _tree(text: str, spans: list[Span]) -> _Node:
    root = _Node(0, len(text))
    stack = [root]
    for span in sorted(spans, key=lambda item: (item.start, -item.end)):
        while len(stack) > 1 and span.start >= stack[-1].end:
            stack.pop()
        parent = stack[-1]
        if span.start >= parent.end:
            continue
        end = min(span.end, parent.end)
        if end <= span.start:
            continue
        node = _Node(span.start, end, span.entity)
        parent.children.append(node)
        stack.append(node)
    return root


def _escape(text: str, *, preformatted: bool = False) -> str:
    # Telegram's exporter turns every newline into <br>, including inside <pre>.
    del preformatted
    escaped = html.escape(text, quote=False)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _url_for_text(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "tg://", "mailto:", "tel:")):
        return text
    return "http://" + text


def _wrap_tag(
    entity: dict[str, Any],
    text: str,
    *,
    emoji_map: dict[int, str] | None,
) -> tuple[str, str, bool] | None:
    """Return (opening tag, closing tag, literal content) for an entity."""

    kind = _scalar(entity.get("_"))
    inner = text

    if kind == "messageEntityBold":
        return "<strong>", "</strong>", False
    if kind == "messageEntityItalic":
        return "<em>", "</em>", False
    if kind == "messageEntityUnderline":
        return "<u>", "</u>", False
    if kind == "messageEntityStrike":
        return "<s>", "</s>", False
    if kind == "messageEntitySpoiler":
        return '<span class="spoiler hidden" onclick="return ShowSpoiler(this)">', "</span>", False
    if kind in {"messageEntityCode", "messageEntityBankCard"}:
        return "<code>", "</code>", False
    if kind == "messageEntityPre":
        return "<pre>", "</pre>", True
    if kind == "messageEntityBlockquote":
        return "<blockquote>", "</blockquote>", False
    if kind == "messageEntityMarked":
        return "<mark>", "</mark>", False
    if kind == "messageEntityTextUrl":
        url = _scalar(entity.get("url")) or _url_for_text(inner)
        return f'<a href="{_attr(url)}">', "</a>", False
    if kind == "messageEntityUrl":
        return f'<a href="{_attr(_url_for_text(inner))}">', "</a>", False
    if kind == "messageEntityEmail":
        return f'<a href="mailto:{_attr(inner)}">', "</a>", False
    if kind == "messageEntityPhone":
        return f'<a href="tel:{_attr(inner)}">', "</a>", False
    if kind == "messageEntityMention":
        username = inner.lstrip("@")
        return f'<a href="{EITAA_PROFILE_BASE}/{_attr(username)}">', "</a>", False
    if kind == "messageEntityMentionName":
        user_id = entity.get("user_id")
        url = f"{EITAA_PROFILE_BASE}/{user_id}" if isinstance(user_id, int) else EITAA_PROFILE_BASE
        return f'<a href="{url}">', "</a>", False
    if kind == "messageEntityHashtag":
        tag = inner.lstrip("#")
        return (
            '<a href="" onclick="return ShowHashtag(&quot;' + _attr(tag) + '&quot;)">',
            "</a>",
            False,
        )
    if kind == "messageEntityCashtag":
        tag = inner.lstrip("$")
        return (
            '<a href="" onclick="return ShowCashtag(&quot;' + _attr(tag) + '&quot;)">',
            "</a>",
            False,
        )
    if kind == "messageEntityBotCommand":
        command = inner.lstrip("/")
        return (
            '<a href="" onclick="return ShowBotCommand(&quot;' + _attr(command) + '&quot;)">',
            "</a>",
            False,
        )
    if kind == "messageEntityCustomEmoji":
        document_id = entity.get("document_id")
        target = (emoji_map or {}).get(document_id) if isinstance(document_id, int) else None
        if target:
            return f'<a href="{_attr(target)}">', "</a>", False
        return None
    return None


def _render_node(
    node: _Node,
    text: str,
    *,
    emoji_map: dict[int, str] | None,
    literal: bool = False,
) -> str:
    if literal:
        return _escape(text[node.start : node.end], preformatted=True)

    parts: list[str] = []
    position = node.start
    for child in node.children:
        if child.start > position:
            parts.append(_escape(text[position : child.start], preformatted=False))
        tag = _wrap_tag(child.entity or {}, text[child.start : child.end], emoji_map=emoji_map)
        if tag is None:
            parts.append(
                _render_node(child, text, emoji_map=emoji_map, literal=False)
            )
        else:
            opening, closing, child_literal = tag
            parts.append(opening)
            parts.append(
                _render_node(
                    child,
                    text,
                    emoji_map=emoji_map,
                    literal=child_literal,
                )
            )
            parts.append(closing)
        position = child.end
    if position < node.end:
        parts.append(_escape(text[position : node.end], preformatted=False))
    return "".join(parts)


def render_text(
    text: str | None,
    entities: list[dict[str, Any]] | None = None,
    *,
    emoji_map: dict[int, str] | None = None,
) -> str:
    """Render message text and TL entities as Telegram-style HTML."""

    if not text:
        return ""
    spans = _spans(text, entities)
    if not spans:
        return _escape(text, preformatted=False)
    return _render_node(_tree(text, spans), text, emoji_map=emoji_map)


def plain_text(text: str | None) -> str:
    return text or ""
