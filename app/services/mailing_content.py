"""Canonical mailing text and Telegram entities, shared by every delivery path."""

from dataclasses import dataclass
from urllib.parse import urlparse

from aiogram.types import MessageEntity


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class MailingContent:
    text: str
    entities: list[dict]
    image_file_id: str | None = None

    def telegram_entities(self) -> list[MessageEntity]:
        return [MessageEntity.model_validate(entity) for entity in self.entities]


def _link_at(text: str, start: int):
    """Read one inline link, including balanced/escaped parentheses in its URL."""
    pos = start + 1
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] in "\n[":
            return None
        if text[pos] == "]":
            break
        pos += 1
    if pos == start + 1 or text[pos : pos + 2] != "](":
        return None
    label_end = pos
    pos += 2
    url_start = pos
    depth = 1
    while pos < len(text):
        char = text[pos]
        if char == "\\" and pos + 1 < len(text):
            pos += 2
            continue
        if char.isspace():
            return None
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                url = (
                    text[url_start:pos]
                    .replace("\\)", ")")
                    .replace("\\(", "(")
                    .replace("\\\\", "\\")
                )
                try:
                    parsed = urlparse(url)
                except ValueError:
                    return None
                if parsed.scheme not in {"http", "https", "tg", "mailto"}:
                    return None
                if parsed.scheme in {"http", "https"} and not parsed.netloc:
                    return None
                return pos + 1, label_end, url
        pos += 1
    return None


def normalize_mailing_content(
    text: str, entities=None, image_file_id: str | None = None
) -> MailingContent:
    source = [
        entity.model_dump(mode="json", exclude_none=True)
        if isinstance(entity, MessageEntity)
        else dict(entity)
        for entity in (entities or [])
    ]
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + utf16_length(char))
    protected = [
        (e["offset"], e["offset"] + e["length"])
        for e in source
        if e["type"] in {"code", "pre", "text_link"}
    ]
    # Map original UTF-16 boundaries to output boundaries, retaining label text.
    mapping = list(range(offsets[-1] + 1))
    output = []
    links = []
    pos = 0
    out_length = 0
    while pos < len(text):
        match = _link_at(text, pos) if text[pos] == "[" else None
        if match:
            end, label_end, url = match
            if any(offsets[pos] < b and offsets[end] > a for a, b in protected):
                match = None
        if match:
            end, label_end, url = match
            label = text[pos + 1 : label_end]
            label_length = utf16_length(label)
            for old in range(offsets[pos], offsets[end] + 1):
                mapping[old] = out_length + min(
                    max(old - offsets[pos + 1], 0), label_length
                )
            output.append(label)
            links.append(
                dict(type="text_link", offset=out_length, length=label_length, url=url)
            )
            out_length += label_length
            pos = end
        else:
            # Escaped opening brackets remain literal, including the backslash.
            count = 2 if text[pos] == "\\" and text[pos + 1 : pos + 2] == "[" else 1
            chunk = text[pos : pos + count]
            size = utf16_length(chunk)
            for unit in range(size + 1):
                mapping[offsets[pos] + unit] = out_length + unit
            output.append(chunk)
            out_length += size
            pos += len(chunk)
    result_entities = []
    for entity in source:
        start = entity["offset"]
        end = start + entity["length"]
        if not (0 <= start <= end < len(mapping)):
            raise ValueError("Некорректное форматирование текста")
        remapped = dict(
            entity, offset=mapping[start], length=mapping[end] - mapping[start]
        )
        if remapped["length"]:
            result_entities.append(remapped)
    result_entities.extend(links)
    result_entities.sort(key=lambda entity: (entity["offset"], -entity["length"]))
    return MailingContent("".join(output), result_entities, image_file_id)


def validate_mailing_content(content: MailingContent) -> None:
    if not content.text.strip():
        raise ValueError("Введите непустой текст рассылки")
    limit = 1024 if content.image_file_id else 4096
    if utf16_length(content.text) > limit:
        target = "подписи к фото" if content.image_file_id else "сообщения"
        raise ValueError(
            f"Текст слишком длинный: максимум {limit} символов для {target}. Сократите текст."
        )
