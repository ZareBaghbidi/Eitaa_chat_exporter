from __future__ import annotations

from eitaa_export.text import render_text, utf16_to_index


def _offset(text: str, needle: str) -> int:
    index = text.index(needle)
    return len(text[:index].encode("utf-16-le")) // 2


def _length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_utf16_offsets_with_emoji() -> None:
    text = "😀 سلام دنیا"
    assert utf16_to_index(text, 0) == 0
    assert utf16_to_index(text, 2) == 1
    assert utf16_to_index(text, 3) == 2


def test_plain_text_escapes_and_breaks_lines() -> None:
    assert render_text("a < b & c\nd") == "a &lt; b &amp; c<br>d"


def test_bold_entity() -> None:
    text = "این یک آزمون است"
    entities = [{"_": "messageEntityBold", "offset": _offset(text, "آزمون"), "length": _length("آزمون")}]
    assert render_text(text, entities) == "این یک <strong>آزمون</strong> است"


def test_emoji_entity_offsets() -> None:
    text = "😀 سلام"
    entities = [{"_": "messageEntityBold", "offset": _offset(text, "سلام"), "length": 4}]
    assert render_text(text, entities) == "😀 <strong>سلام</strong>"


def test_pre_is_literal() -> None:
    text = "<b>not bold</b>"
    entities = [{"_": "messageEntityPre", "offset": 0, "length": _length(text)}]
    assert render_text(text, entities) == "<pre>&lt;b&gt;not bold&lt;/b&gt;</pre>"


def test_links_hashtags_and_mentions() -> None:
    text = "https://eitaa.com/x #خبر @ali"
    entities = [
        {"_": "messageEntityUrl", "offset": 0, "length": _length("https://eitaa.com/x")},
        {"_": "messageEntityHashtag", "offset": _offset(text, "#خبر"), "length": _length("#خبر")},
        {"_": "messageEntityMention", "offset": _offset(text, "@ali"), "length": _length("@ali")},
    ]
    html = render_text(text, entities)
    assert '<a href="https://eitaa.com/x">https://eitaa.com/x</a>' in html
    assert 'ShowHashtag(&quot;خبر&quot;)' in html
    assert '<a href="https://eitaa.com/ali">@ali</a>' in html


def test_nested_entities() -> None:
    text = "bold italic"
    entities = [
        {"_": "messageEntityBold", "offset": 0, "length": 11},
        {"_": "messageEntityItalic", "offset": 5, "length": 6},
    ]
    assert render_text(text, entities) == "<strong>bold <em>italic</em></strong>"


def test_spoiler_clickable() -> None:
    text = "secret"
    entities = [{"_": "messageEntitySpoiler", "offset": 0, "length": 6}]
    html = render_text(text, entities)
    assert 'class="spoiler hidden"' in html
    assert "ShowSpoiler(this)" in html
