from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import BirthdayGreetingSettings
from app.services.mailing_content import (
    MailingContent,
    normalize_mailing_content,
    utf16_length,
    validate_mailing_content,
)

NAME_PLACEHOLDER = "{name}"
DEFAULT_TEXT = "🎉 {name}, поздравляем с днём рождения! Ждём вас в «Рыба и гады»."
SETTINGS_ID = 1


@dataclass(frozen=True)
class BirthdayGreeting:
    enabled: bool
    template: MailingContent
    send_time: time = time(10)

    def for_recipient(self, name: str) -> MailingContent:
        parts = self.template.text.split(NAME_PLACEHOLDER)
        if len(parts) == 1:
            return self.template

        token_length = len(NAME_PLACEHOLDER)
        length_change = utf16_length(name) - token_length
        token_starts = []
        position = 0
        for part in parts[:-1]:
            position += utf16_length(part)
            token_starts.append(position)
            position += token_length

        def move_offset(offset: int) -> int:
            shift = 0
            for start in token_starts:
                if offset <= start:
                    break
                if offset < start + token_length:
                    # A boundary within the placeholder must not split the name.
                    return start + shift
                shift += length_change
            return offset + shift

        entities = []
        for entity in self.template.entities:
            offset = move_offset(entity["offset"])
            end = move_offset(entity["offset"] + entity["length"])
            if end > offset:
                entities.append(dict(entity, offset=offset, length=end - offset))
        return MailingContent(name.join(parts), entities, self.template.image_file_id)


class BirthdayGreetingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _snapshot(item: BirthdayGreetingSettings | None) -> BirthdayGreeting:
        if item is None:
            return BirthdayGreeting(True, MailingContent(DEFAULT_TEXT, []))
        return BirthdayGreeting(
            item.enabled,
            MailingContent(
                item.text or DEFAULT_TEXT, item.entities or [], item.image_file_id
            ),
            item.send_time,
        )

    async def get(self) -> BirthdayGreeting:
        item = await self.session.get(BirthdayGreetingSettings, SETTINGS_ID)
        return self._snapshot(item)

    async def _for_update(self) -> BirthdayGreetingSettings:
        # Concurrent first edits must create a single row before validating changes.
        insert = (
            postgres_insert
            if self.session.get_bind().dialect.name == "postgresql"
            else sqlite_insert
        )
        await self.session.execute(
            insert(BirthdayGreetingSettings)
            .values(id=SETTINGS_ID)
            .on_conflict_do_nothing(index_elements=[BirthdayGreetingSettings.id])
        )
        item = await self.session.get(
            BirthdayGreetingSettings,
            SETTINGS_ID,
            with_for_update=True,
            populate_existing=True,
        )
        assert item is not None
        return item

    @staticmethod
    def _validate(content: MailingContent) -> None:
        # User.first_name stores up to 100 Unicode characters, up to 200 UTF-16 units.
        longest = BirthdayGreeting(True, content).for_recipient("🙂" * 100)
        validate_mailing_content(longest)

    async def set_enabled(self, enabled: bool) -> None:
        item = await self._for_update()
        item.enabled = enabled
        await self.session.commit()

    async def set_send_time(self, send_time: time) -> None:
        item = await self._for_update()
        item.send_time = send_time
        await self.session.commit()

    async def claim_for_delivery(self, now: datetime) -> BirthdayGreeting | None:
        day = now.date()
        minute = now.time().replace(second=0, microsecond=0)

        def is_due(
            item: BirthdayGreetingSettings | None, greeting: BirthdayGreeting
        ) -> bool:
            return (
                greeting.enabled
                and greeting.send_time == minute
                and (item is None or item.last_run_date != day)
            )

        item = await self.session.get(BirthdayGreetingSettings, SETTINGS_ID)
        if not is_due(item, self._snapshot(item)):
            return None

        item = await self._for_update()
        greeting = self._snapshot(item)
        if not is_due(item, greeting):
            await self.session.rollback()
            return None

        # Changing the time after delivery must not start a second run that day.
        item.last_run_date = day
        await self.session.commit()
        return greeting

    async def set_text(self, text: str, entities=None) -> None:
        item = await self._for_update()
        try:
            content = normalize_mailing_content(text, entities, item.image_file_id)
            self._validate(content)
        except ValueError:
            await self.session.rollback()
            raise
        item.text = content.text
        item.entities = content.entities
        await self.session.commit()

    async def set_image(self, file_id: str | None) -> None:
        item = await self._for_update()
        content = MailingContent(
            item.text or DEFAULT_TEXT, item.entities or [], file_id
        )
        try:
            self._validate(content)
        except ValueError:
            await self.session.rollback()
            raise
        item.image_file_id = file_id
        await self.session.commit()
