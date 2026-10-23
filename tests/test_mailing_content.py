from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import MessageEntity

from app.bot.handlers.admin import send_mailing_preview
from app.bot.mailing_transport import send_mailing
from app.services.mailing_content import (
    normalize_mailing_content,
    validate_mailing_content,
    utf16_length,
)


def test_markdown_links_preserve_unicode_native_formatting_and_remap_auto_urls():
    text = "🐟 [Меню](https://example.com/menu_(new)?a=1&b=2) и сайт"
    native_start = utf16_length(text[:-4])
    url_start = utf16_length(text[: text.index("https")])
    url_end = utf16_length(text[: text.index(" и") - 1])
    entities = [
        MessageEntity(type="bold", offset=0, length=utf16_length(text)),
        MessageEntity(
            type="text_link",
            offset=native_start,
            length=4,
            url="https://native.example",
        ),
        MessageEntity(type="url", offset=url_start, length=url_end - url_start),
    ]
    content = normalize_mailing_content(text, entities)
    assert content.text == "🐟 Меню и сайт"
    assert content.entities == [
        dict(type="bold", offset=0, length=utf16_length(content.text)),
        dict(
            type="text_link",
            offset=3,
            length=4,
            url="https://example.com/menu_(new)?a=1&b=2",
        ),
        dict(type="text_link", offset=10, length=4, url="https://native.example"),
    ]
    assert normalize_mailing_content(content.text, content.entities) == content


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[a](https://a.com) [б](https://b.com)", "a б"),
        (r"[a](https://a.com/a\)b)", "a"),
        ("[сломано](https://a.com", "[сломано](https://a.com"),
        ("[нет](не ссылка)", "[нет](не ссылка)"),
        ("[пусто]()", "[пусто]()"),
        ("[нет](https://)", "[нет](https://)"),
        ("[нет](https://[bad-ipv6/)", "[нет](https://[bad-ipv6/)"),
        (r"\[буквально](https://a.com)", r"\[буквально](https://a.com)"),
        ("<b>текст</b> & привет_мир!", "<b>текст</b> & привет_мир!"),
    ],
)
def test_link_syntax_and_plain_text(text, expected):
    assert normalize_mailing_content(text).text == expected


@pytest.mark.parametrize("kind", ["code", "pre", "text_link"])
def test_protected_entities_are_not_parsed(kind):
    text = "[так](https://example.com)"
    entity = dict(type=kind, offset=0, length=len(text))
    if kind == "text_link":
        entity["url"] = "https://native.example"
    content = normalize_mailing_content(text, [entity])
    assert content.text == text
    assert content.entities == [entity]


@pytest.mark.parametrize("photo,limit", [(None, 4096), ("photo-id", 1024)])
def test_length_validation_uses_rendered_text(photo, limit):
    validate_mailing_content(
        normalize_mailing_content("a" * limit, image_file_id=photo)
    )
    with pytest.raises(ValueError, match="Сократите"):
        validate_mailing_content(
            normalize_mailing_content("a" * (limit + 1), image_file_id=photo)
        )
    validate_mailing_content(
        normalize_mailing_content(
            "[" + "a" * limit + "](https://a.com)", image_file_id=photo
        )
    )


@pytest.mark.parametrize("photo", [None, "photo-id"])
async def test_preview_and_transport_have_identical_content(tmp_path, photo):
    content = normalize_mailing_content("🐟 [Меню](https://a.com)", image_file_id=photo)
    settings = SimpleNamespace(assets_dir=tmp_path)
    item = SimpleNamespace(
        id=1,
        name="Тест",
        status=SimpleNamespace(value="draft"),
        text=content.text,
        entities=content.entities,
        image_file_id=photo,
        runs=[],
        archived_at=None,
    )
    message = SimpleNamespace(
        reply_markup=None, edit_text=AsyncMock(), edit_media=AsyncMock()
    )
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    await send_mailing_preview(message, item, settings)
    await send_mailing(bot, settings, 42, content)
    if photo:
        preview = message.edit_media.await_args
        media = preview.kwargs["media"]
        sent = bot.send_photo.await_args
        assert media.caption == "Рассылка «Тест»\n\n" + sent.kwargs["caption"]
        preview_entities = media.caption_entities
        sent_entities = sent.kwargs["caption_entities"]
        assert media.parse_mode is sent.kwargs["parse_mode"] is None
    else:
        preview = message.edit_text.await_args
        sent = bot.send_message.await_args
        assert preview.args[0] == "Рассылка «Тест»\n\n" + sent.args[1]
        preview_entities = preview.kwargs["entities"]
        sent_entities = sent.kwargs["entities"]
        assert preview.kwargs["parse_mode"] is sent.kwargs["parse_mode"] is None
    assert sent_entities == content.telegram_entities()
    assert preview_entities[0].offset == sent_entities[0].offset + 17
    assert preview_entities[0].url == sent_entities[0].url
    assert preview_entities[0].length == sent_entities[0].length
