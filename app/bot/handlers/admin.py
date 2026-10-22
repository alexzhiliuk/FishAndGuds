from datetime import datetime
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import (
    admin_legal_links_keyboard,
    admin_menu,
    admin_restaurant_links_keyboard,
    admin_restaurants_keyboard,
    back_keyboard,
    document_edit_keyboard,
    mailing_actions,
    mailing_input_back,
    mailing_image_keyboard,
    mailing_purge_keyboard,
    mailing_list_keyboard,
    mailing_history_keyboard,
    mailing_run_keyboard,
    restaurant_link_edit_keyboard,
)
from app.bot.media import resolve_mailing_photo
from app.bot.mailing_report import mailing_report, mailing_run_report
from app.bot.mailing_transport import send_mailing
from app.bot.navigation import (
    answer_photo_with_buttons,
    answer_with_buttons,
    clear_inline_keyboard,
)
from app.bot.states import (
    ApplicationDocumentEdit,
    MailingCreate,
    MailingEdit,
    MailingSchedule,
    RestaurantLinkEdit,
)
from app.config import Settings
from app.repositories import UserRepository
from app.services import (
    ApplicationSettingsService,
    MailingService,
    PhoneNormalizationService,
    RestaurantService,
)
from app.services.mailing_content import (
    MailingContent,
    normalize_mailing_content,
    validate_mailing_content,
)
from app.services.mailings import utc
from app.models.entities import RunStatus

router = Router(name="admin")
logger = logging.getLogger(__name__)
RESTAURANT_LINK_FIELDS = {
    "booking_url": "🍽 Бронирование, если iiko не передал ссылку",
    "delivery_url": "🛵 Доставка",
    "reviews_url": "⭐ Отзывы / Яндекс Карты",
    "contact_phone": "📞 Телефон",
}
APPLICATION_DOCUMENTS = {
    ApplicationSettingsService.PRIVACY_POLICY: "🔒 Политика обработки персональных данных",
    ApplicationSettingsService.LOYALTY_RULES: "📜 Правила программы лояльности",
}
LEGACY_DOCUMENT_KEYS = {
    "privacy_policy_url": ApplicationSettingsService.PRIVACY_POLICY,
    "loyalty_rules_url": ApplicationSettingsService.LOYALTY_RULES,
}


def allowed(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


async def deny(event, settings):
    if allowed(event.from_user.id, settings):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Доступ запрещён", show_alert=True)
    else:
        await event.answer("Доступ запрещён")
    return True


async def send_mailing_preview(
    message: Message, item, settings: Settings, list_page: int = 0
):
    """Render a mailing exactly as a recipient will see it, plus admin actions."""
    latest = max((run.id for run in getattr(item, "runs", [])), default=0)
    reply_markup = mailing_actions(
        item.id,
        item.status.value,
        list_page,
        latest,
        bool(getattr(item, "archived_at", None)),
    )
    content = normalize_mailing_content(
        item.text, getattr(item, "entities", None), item.image_file_id
    )
    photo = resolve_mailing_photo(item.image_file_id, settings.assets_dir)
    if photo:
        await answer_photo_with_buttons(
            message,
            photo,
            content.text,
            reply_markup=reply_markup,
            caption_entities=content.telegram_entities(),
            parse_mode=None,
        )
    else:
        await answer_with_buttons(
            message,
            content.text,
            reply_markup=reply_markup,
            entities=content.telegram_entities(),
            parse_mode=None,
        )


@router.message(Command("removeme"))
async def remove_me(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(message, settings):
        return
    removed = await UserRepository(session).delete_by_telegram_id(message.from_user.id)
    await state.clear()
    await state.update_data(admin_local_registration_only=True)
    text = (
        "Локальный профиль удалён. Данные в iiko не изменены."
        if removed
        else "Локальный профиль уже отсутствует. Данные в iiko не изменены."
    )
    await message.answer(
        f"{text}\n\nОтправьте /start и пройдите регистрацию заново. "
        "Бот подключит только уже существующего гостя iiko."
    )


@router.message(F.text == "⚙️ Админ-панель")
async def open_admin(message: Message, settings: Settings):
    if await deny(message, settings):
        return
    await answer_with_buttons(
        message, "⚙️ Административное меню", reply_markup=admin_menu()
    )


@router.callback_query(F.data == "menu:admin")
async def open_admin_callback(callback: CallbackQuery, settings: Settings):
    if await deny(callback, settings):
        return
    await answer_with_buttons(
        callback.message, "⚙️ Административное меню", reply_markup=admin_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "admin:back")
async def admin_back(callback: CallbackQuery, settings: Settings):
    if await deny(callback, settings):
        return
    await answer_with_buttons(
        callback.message, "⚙️ Административное меню", reply_markup=admin_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "admin:user_count")
async def admin_user_count(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    count = await UserRepository(session).count_all()
    await answer_with_buttons(
        callback.message,
        f"👥 Всего пользователей в базе: {count}",
        reply_markup=admin_menu(),
    )
    await callback.answer()


def legal_admin_text(documents: dict) -> str:
    lines = ["📄 Документы регистрации", ""]
    for key, label in APPLICATION_DOCUMENTS.items():
        document = documents.get(key)
        status = f"загружен файл {document.file_name}" if document else "не загружен"
        lines.append(f"{label}:\n{status}\n")
    lines.append("PDF-файлы отправляются пользователям через бота и доступны в анкете.")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:legal")
async def admin_legal(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    await callback.answer()
    await state.clear()
    documents = await ApplicationSettingsService(session).registration_documents()
    await answer_with_buttons(
        callback.message,
        legal_admin_text(documents),
        reply_markup=admin_legal_links_keyboard(),
    )


@router.callback_query(F.data.startswith("legal_document:clear:"))
async def legal_document_clear(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    key = callback.data.rsplit(":", 1)[1]
    if key not in APPLICATION_DOCUMENTS:
        await callback.answer("Неизвестный документ", show_alert=True)
        return
    await ApplicationSettingsService(session).clear_document(key)
    await state.clear()
    documents = await ApplicationSettingsService(session).registration_documents()
    await callback.answer("Файл удалён")
    await answer_with_buttons(
        callback.message,
        legal_admin_text(documents),
        reply_markup=admin_legal_links_keyboard(),
    )


@router.callback_query(F.data.startswith("legal_document:"))
@router.callback_query(F.data.startswith("legal_link:"))
async def legal_document_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings
):
    if await deny(callback, settings):
        return
    key = callback.data.split(":", 1)[1]
    key = LEGACY_DOCUMENT_KEYS.get(key, key)
    if key not in APPLICATION_DOCUMENTS:
        await callback.answer("Неизвестный документ", show_alert=True)
        return
    await callback.answer()
    await state.update_data(application_document_key=key)
    await state.set_state(ApplicationDocumentEdit.value)
    await answer_with_buttons(
        callback.message,
        f"Отправьте PDF-файл для раздела «{APPLICATION_DOCUMENTS[key]}».",
        reply_markup=document_edit_keyboard(key),
    )


@router.message(ApplicationDocumentEdit.value)
async def legal_document_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(message, settings):
        return
    data = await state.get_data()
    key = data["application_document_key"]
    document = message.document
    file_name = document.file_name if document else None
    if (
        document is None
        or document.mime_type != "application/pdf"
        or not file_name
        or not file_name.lower().endswith(".pdf")
    ):
        await answer_with_buttons(
            message,
            "Нужен файл в формате PDF.",
            reply_markup=document_edit_keyboard(key),
        )
        return
    await ApplicationSettingsService(session).update_document(
        key, document.file_id, file_name
    )
    documents = await ApplicationSettingsService(session).registration_documents()
    await state.clear()
    await answer_with_buttons(
        message,
        "PDF-файл сохранён.\n\n" + legal_admin_text(documents),
        reply_markup=admin_legal_links_keyboard(),
    )


def restaurant_admin_text(item) -> str:
    lines = [
        f"🏪 {item.name}",
        item.address or "Адрес не указан",
        "",
        f"🌐 Бронирование из iiko: {item.website_url or 'не передано'}",
        "",
        "Настройки из админки:",
    ]
    for field, label in RESTAURANT_LINK_FIELDS.items():
        lines.append(f"{label}: {getattr(item, field) or 'не задана'}")
    lines.append("\nЭти значения настраиваются отдельно для каждого ресторана.")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:restaurants")
async def admin_restaurants(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    await callback.answer()
    await state.clear()
    items = await RestaurantService(session).list_all()
    text = "🏪 Выберите ресторан:" if items else "Ресторанов в базе пока нет."
    await answer_with_buttons(
        callback.message, text, reply_markup=admin_restaurants_keyboard(items)
    )


@router.callback_query(F.data.startswith("admin:restaurant:"))
async def admin_restaurant(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    await callback.answer()
    await state.clear()
    item_id = int(callback.data.rsplit(":", 1)[1])
    item = await RestaurantService(session).get(item_id)
    if item is None:
        await answer_with_buttons(
            callback.message,
            "Ресторан не найден.",
            reply_markup=back_keyboard("admin:restaurants"),
        )
        return
    await answer_with_buttons(
        callback.message,
        restaurant_admin_text(item),
        reply_markup=admin_restaurant_links_keyboard(
            item.id, allow_delivery=item.delivery_configurable
        ),
    )


@router.callback_query(F.data.startswith("restaurant_link:clear:"))
async def restaurant_link_clear(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    _, _, field, item_id = callback.data.split(":")
    if field not in RESTAURANT_LINK_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    if field == "delivery_url":
        restaurant = await RestaurantService(session).get(int(item_id))
        if restaurant is None or not restaurant.delivery_configurable:
            await callback.answer(
                "Доставка настраивается только для ресторана", show_alert=True
            )
            return
    item = await RestaurantService(session).update_local_link(int(item_id), field, None)
    await state.clear()
    await callback.answer("Поле очищено")
    await answer_with_buttons(
        callback.message,
        restaurant_admin_text(item),
        reply_markup=admin_restaurant_links_keyboard(
            item.id, allow_delivery=item.delivery_configurable
        ),
    )


@router.callback_query(F.data.startswith("restaurant_link:"))
async def restaurant_link_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    _, field, item_id = callback.data.split(":")
    if field not in RESTAURANT_LINK_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    if field == "delivery_url":
        item = await RestaurantService(session).get(int(item_id))
        if item is None or not item.delivery_configurable:
            await callback.answer(
                "Доставка настраивается только для ресторана", show_alert=True
            )
            return
    await callback.answer()
    await state.update_data(restaurant_id=int(item_id), restaurant_link_field=field)
    await state.set_state(RestaurantLinkEdit.value)
    if field == "contact_phone":
        instruction = f"Отправьте номер для раздела «{RESTAURANT_LINK_FIELDS[field]}»."
    else:
        instruction = (
            f"Отправьте ссылку для раздела «{RESTAURANT_LINK_FIELDS[field]}».\n\n"
            "Разрешены ссылки http:// и https://."
        )
    await answer_with_buttons(
        callback.message,
        instruction,
        reply_markup=restaurant_link_edit_keyboard(field, int(item_id)),
    )


@router.message(RestaurantLinkEdit.value)
async def restaurant_link_value(
    message: Message, state: FSMContext, session: AsyncSession
):
    raw_value = (message.text or "").strip()
    data = await state.get_data()
    field = data["restaurant_link_field"]
    if raw_value.lower() in {"удалить", "очистить", "нет", "-"}:
        value = None
    elif field == "contact_phone":
        try:
            value = PhoneNormalizationService.normalize(raw_value)
        except ValueError:
            await answer_with_buttons(
                message,
                "Некорректный номер телефона. Отправьте номер с кодом страны.",
                reply_markup=restaurant_link_edit_keyboard(
                    field, data["restaurant_id"]
                ),
            )
            return
    else:
        parsed = urlparse(raw_value)
        if (
            len(raw_value) > 500
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            await answer_with_buttons(
                message,
                "Некорректная ссылка. Отправьте полный адрес, начинающийся с https://.",
                reply_markup=restaurant_link_edit_keyboard(
                    field, data["restaurant_id"]
                ),
            )
            return
        value = raw_value
    item = await RestaurantService(session).update_local_link(
        data["restaurant_id"], field, value
    )
    await state.clear()
    await answer_with_buttons(
        message,
        "Ссылка сохранена.\n\n" + restaurant_admin_text(item),
        reply_markup=admin_restaurant_links_keyboard(
            item.id, allow_delivery=item.delivery_configurable
        ),
    )


@router.callback_query(F.data == "admin:create")
async def create_start(callback: CallbackQuery, state: FSMContext, settings: Settings):
    if await deny(callback, settings):
        return
    await state.set_state(MailingCreate.name)
    await answer_with_buttons(
        callback.message,
        "Введите название рассылки:",
        reply_markup=mailing_input_back("mail:create_back"),
    )
    await callback.answer()


@router.callback_query(F.data == "mail:create_back")
async def create_back(callback: CallbackQuery, state: FSMContext, settings: Settings):
    if await deny(callback, settings):
        return
    await state.clear()
    await answer_with_buttons(
        callback.message, "⚙️ Административное меню", reply_markup=admin_menu()
    )
    await callback.answer()


@router.message(MailingCreate.name)
async def create_name(message: Message, state: FSMContext, settings: Settings):
    if await deny(message, settings):
        return
    if not message.text or len(message.text) > 200:
        await message.answer("Введите название от 1 до 200 символов.")
        return
    await state.update_data(name=message.text)
    await state.set_state(MailingCreate.text)
    await answer_with_buttons(
        message,
        "Введите текст рассылки. Ссылку можно оформить через меню Telegram или как [текст](https://example.com/):",
        reply_markup=mailing_input_back("mail:create_back"),
    )


@router.message(MailingCreate.text)
async def create_text(message: Message, state: FSMContext, settings: Settings):
    if await deny(message, settings):
        return
    try:
        content = normalize_mailing_content(message.text or "", message.entities)
        validate_mailing_content(content)
    except ValueError as exc:
        await message.answer(
            str(exc), reply_markup=mailing_input_back("mail:create_back")
        )
        return
    await state.update_data(text=content.text, entities=content.entities)
    await state.set_state(MailingCreate.image)
    await answer_with_buttons(
        message,
        "Отправьте изображение или нажмите «Без фото»:",
        reply_markup=mailing_image_keyboard(),
    )


@router.message(MailingCreate.image)
async def create_image(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
):
    if await deny(message, settings):
        return
    image = message.photo[-1].file_id if message.photo else None
    if not image and (message.text or "").lower() not in {"без фото", "нет", "-"}:
        await answer_with_buttons(
            message,
            "Отправьте фото или нажмите «Без фото».",
            reply_markup=mailing_image_keyboard(),
        )
        return
    await finish_mailing_creation(message, state, session, image)


@router.callback_query(F.data == "mail:without_photo")
async def create_without_photo(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    if await state.get_state() != MailingCreate.image.state:
        await callback.answer(
            "Эта кнопка устарела. Откройте создание рассылки заново.", show_alert=True
        )
        return
    await callback.answer()
    await finish_mailing_creation(callback.message, state, session, None)


async def finish_mailing_creation(message, state, session, image):
    data = await state.get_data()
    try:
        item = await MailingService(session).create(
            data["name"], data["text"], image, entities=data.get("entities")
        )
    except ValueError as exc:
        await state.set_state(MailingCreate.text)
        await message.answer(
            str(exc), reply_markup=mailing_input_back("mail:create_back")
        )
        return
    await state.clear()
    await answer_with_buttons(
        message,
        f"Рассылка #{item.id} создана.",
        reply_markup=mailing_actions(item.id, item.status.value),
    )


async def show_mailing_list(
    message: Message, session: AsyncSession, page: int, archived: bool = False
):
    page = max(page, 0)
    service = MailingService(session)
    items, has_next = (
        await service.page(page, size=5, archived=True)
        if archived
        else await service.page(page)
    )
    if not items and page > 0:
        page -= 1
        items, has_next = (
            await service.page(page, size=5, archived=True)
            if archived
            else await service.page(page)
        )
    text = (
        ("🗃 Архив рассылок:" if items else "Архив пока пуст.")
        if archived
        else ("📨 Выберите рассылку по названию:" if items else "Рассылок пока нет.")
    )
    markup = mailing_list_keyboard(items, page, has_next, archived=archived)
    await answer_with_buttons(message, text, reply_markup=markup)


@router.callback_query(F.data == "admin:list")
@router.callback_query(F.data.startswith("admin:list:"))
async def list_mailings(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    page = int(callback.data.rsplit(":", 1)[1]) if callback.data.count(":") == 2 else 0
    await show_mailing_list(callback.message, session, page)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:list_back:"))
async def mailing_list_back(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    page = int(callback.data.rsplit(":", 1)[1])
    await show_mailing_list(callback.message, session, page)
    await callback.answer()


@router.callback_query(F.data.startswith("mail:open:"))
@router.callback_query(F.data.startswith("mail:archived:"))
async def open_mailing(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, item_id, page = callback.data.split(":")
    item = await MailingService(session).get(int(item_id))
    if not item:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    await send_mailing_preview(callback.message, item, settings, int(page))
    await callback.answer()


@router.callback_query(F.data.startswith("mail:edit_"))
async def edit_start(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
):
    if await deny(callback, settings):
        return
    _, action, item_id, *page_parts = callback.data.split(":")
    field = action.removeprefix("edit_")
    item = await MailingService(session).get(int(item_id))
    if item is None or item.archived_at is not None or item.status.value == "sending":
        await callback.answer(
            "Редактирование недоступно. Откройте рассылку заново.", show_alert=True
        )
        return
    list_page = int(page_parts[0]) if page_parts else 0
    states = {
        "name": MailingEdit.name,
        "text": MailingEdit.text,
        "image": MailingEdit.image,
    }
    await state.update_data(mailing_id=int(item_id), field=field, list_page=list_page)
    await state.set_state(states[field])
    await answer_with_buttons(
        callback.message,
        "Отправьте новое значение:"
        if field != "image"
        else "Отправьте новое изображение:",
        reply_markup=mailing_input_back(f"mail:input_back:{item_id}:{list_page}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mail:input_back:"))
async def mailing_input_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    if await deny(callback, settings):
        return
    _, _, item_id, page = callback.data.split(":")
    await state.clear()
    item = await MailingService(session).get(int(item_id))
    if not item:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    await send_mailing_preview(callback.message, item, settings, int(page))
    await callback.answer()


@router.message(MailingEdit.name)
@router.message(MailingEdit.text)
@router.message(MailingEdit.image)
async def edit_value(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
):
    if await deny(message, settings):
        return
    data = await state.get_data()
    field = data["field"]
    value = (
        message.photo[-1].file_id
        if field == "image" and message.photo
        else message.text
    )
    if not value:
        await answer_with_buttons(
            message,
            "Некорректное значение.",
            reply_markup=mailing_input_back(
                f"mail:input_back:{data['mailing_id']}:{data.get('list_page', 0)}"
            ),
        )
        return
    changes = {"image_file_id": value} if field == "image" else {field: value}
    if field == "text":
        changes["entities"] = message.entities
    try:
        item = await MailingService(session).update(data["mailing_id"], **changes)
    except ValueError as exc:
        await message.answer(
            str(exc),
            reply_markup=mailing_input_back(
                f"mail:input_back:{data['mailing_id']}:{data.get('list_page', 0)}"
            ),
        )
        return
    await state.clear()
    item = await MailingService(session).get(item.id)
    await message.answer("Рассылка обновлена.")
    await send_mailing_preview(message, item, settings, data.get("list_page", 0))


async def telegram_sender(
    bot: Bot, settings: Settings, telegram_id: int, content: MailingContent
):
    return await send_mailing(bot, settings, telegram_id, content)


@router.callback_query(F.data.startswith("mail:send:"))
async def send_now(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
):
    if await deny(callback, settings):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer(
            "Откройте рассылку заново: эта кнопка устарела.", show_alert=True
        )
        return
    item_id, expected_run_id = int(parts[2]), int(parts[3])
    await callback.answer("Рассылка запущена")
    await clear_inline_keyboard(callback.message)
    await callback.message.answer(
        "Отправка выполняется. Прогресс доступен в истории.",
        reply_markup=mailing_actions(item_id, "sending"),
    )
    try:
        run = await MailingService(session).send(
            item_id,
            lambda uid, content: telegram_sender(bot, settings, uid, content),
            include_telegram_ids=settings.admin_ids,
            expected_run_id=expected_run_id,
        )
    except ValueError as exc:
        await callback.message.answer(str(exc), reply_markup=admin_menu())
        return
    await callback.message.answer(mailing_report(run), reply_markup=admin_menu())


@router.callback_query(F.data.startswith("mail:schedule:"))
async def schedule_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, item_id, *page_parts = callback.data.split(":")
    list_page = int(page_parts[0]) if page_parts else 0
    await state.update_data(mailing_id=int(item_id), list_page=list_page)
    await state.set_state(MailingSchedule.when)
    await answer_with_buttons(
        callback.message,
        "Введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ:",
        reply_markup=mailing_input_back(f"mail:input_back:{item_id}:{list_page}"),
    )
    await callback.answer()


@router.message(MailingSchedule.when)
async def schedule_value(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
):
    if await deny(message, settings):
        return
    data = await state.get_data()
    back = mailing_input_back(
        f"mail:input_back:{data['mailing_id']}:{data.get('list_page', 0)}"
    )
    try:
        when = datetime.strptime(message.text, "%d.%m.%Y %H:%M").replace(
            tzinfo=ZoneInfo(settings.timezone)
        )
    except (TypeError, ValueError):
        await answer_with_buttons(
            message, "Неверный формат. Пример: 20.08.2026 14:30", reply_markup=back
        )
        return
    if when <= datetime.now(ZoneInfo(settings.timezone)):
        await answer_with_buttons(
            message, "Время должно быть в будущем.", reply_markup=back
        )
        return
    try:
        item = await MailingService(session).schedule(data["mailing_id"], when)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=back)
        return
    await state.clear()
    item = await MailingService(session).get(item.id)
    await message.answer(f"Рассылка запланирована на {when:%d.%m.%Y %H:%M}.")
    await send_mailing_preview(message, item, settings, data.get("list_page", 0))


@router.callback_query(F.data.startswith("mail:cancel:"))
async def cancel(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    if await deny(callback, settings):
        return
    try:
        await MailingService(session).cancel(int(callback.data.rsplit(":", 1)[1]))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await clear_inline_keyboard(callback.message)
    await callback.message.answer("Рассылка отменена.")
    await callback.answer()


@router.callback_query(F.data.startswith("mail:delete:"))
async def delete(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    if await deny(callback, settings):
        return
    _, _, item_id, *page_parts = callback.data.split(":")
    list_page = int(page_parts[0]) if page_parts else 0
    try:
        await MailingService(session).delete(int(item_id))
    except ValueError as exc:
        if str(exc) != "Рассылка не найдена":
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Рассылка перенесена в архив")
    await clear_inline_keyboard(callback.message)
    await show_mailing_list(callback.message, session, list_page)


@router.callback_query(F.data.startswith("mail:archive_list:"))
async def archived_mailings(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    await show_mailing_list(
        callback.message, session, int(callback.data.rsplit(":", 1)[1]), archived=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mail:purge_prompt:"))
async def prompt_purge_archived(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, item_id, page = callback.data.split(":")
    item = await MailingService(session).get(int(item_id))
    if item is None:
        await callback.answer("Рассылка уже удалена", show_alert=True)
        return
    if item.archived_at is None:
        await callback.answer(
            "Окончательное удаление доступно только из архива", show_alert=True
        )
        return
    await answer_with_buttons(
        callback.message,
        f"Удалить рассылку «{item.name}» навсегда?\n\n"
        "Шаблон и вся история отправок будут удалены без возможности восстановления.\n"
        "Отправленные сообщения останутся у получателей. Удалить их через бота после этого будет невозможно.",
        reply_markup=mailing_purge_keyboard(item.id, int(page)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mail:purge_confirm:"))
async def purge_archived(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, item_id, page = callback.data.split(":")
    try:
        deleted = await MailingService(session).purge_archived(int(item_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(
        "Рассылка удалена навсегда" if deleted else "Рассылка уже удалена"
    )
    await show_mailing_list(callback.message, session, int(page), archived=True)


@router.callback_query(F.data.startswith("mail:history:"))
async def mailing_history(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, item_id, page, list_page, _archived = callback.data.split(":")
    service = MailingService(session)
    item = await service.get(int(item_id))
    if item is None:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    page_num = max(int(page), 0)
    runs, has_next = await service.runs_page(item.id, page_num)
    if not runs and page_num:
        page_num -= 1
        runs, has_next = await service.runs_page(item.id, page_num)
    markup = mailing_history_keyboard(
        item.id,
        runs,
        page_num,
        has_next,
        int(list_page),
        item.archived_at is not None,
        ZoneInfo(settings.timezone),
    )
    await answer_with_buttons(
        callback.message,
        f"📜 История отправок: {item.name}\n"
        + ("Выберите запуск:" if runs else "Отправок пока нет."),
        reply_markup=markup,
    )
    await callback.answer()


async def show_mailing_run(
    message, service, run, settings, history_page, list_page, archived
):
    counts, _deadline = await service.deletion_summary(run.id)
    if run.snapshot_text is not None:
        content = MailingContent(
            run.snapshot_text, run.snapshot_entities or [], run.snapshot_image_file_id
        )
        photo = resolve_mailing_photo(content.image_file_id, settings.assets_dir)
        if photo:
            await message.answer_photo(
                photo,
                caption=content.text,
                caption_entities=content.telegram_entities(),
                parse_mode=None,
            )
        else:
            await message.answer(
                content.text, entities=content.telegram_entities(), parse_mode=None
            )
    markup = mailing_run_keyboard(
        run,
        history_page,
        list_page,
        archived,
        counts["available"] > 0
        and run.status != RunStatus.sending
        and run.deletion_status != "deleting",
    )
    await answer_with_buttons(
        message, mailing_run_report(run, counts, settings.timezone), reply_markup=markup
    )


@router.callback_query(F.data.startswith("mail:run:"))
async def open_mailing_run(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, run_id, history_page, list_page, _archived = callback.data.split(":")
    service = MailingService(session)
    try:
        run = await service.refresh_run(int(run_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    item = await service.get(run.mailing_id)
    await callback.answer()
    await show_mailing_run(
        callback.message,
        service,
        run,
        settings,
        int(history_page),
        int(list_page),
        item.archived_at is not None,
    )


@router.callback_query(F.data.startswith("mail:remove:"))
async def confirm_mailing_removal(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, run_id, history_page, list_page, _archived = callback.data.split(":")
    service = MailingService(session)
    try:
        run = await service.refresh_run(int(run_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    counts, _deadline = await service.deletion_summary(run.id)
    if (
        not counts["available"]
        or run.snapshot_text is None
        or run.status == RunStatus.sending
        or run.deletion_status == "deleting"
    ):
        await callback.answer(
            "Сейчас нет сообщений, доступных для удаления. Обновите статус запуска.",
            show_alert=True,
        )
        return
    item = await service.get(run.mailing_id)
    date = utc(run.started_at).astimezone(ZoneInfo(settings.timezone))
    await answer_with_buttons(
        callback.message,
        f"Удалить сообщения запуска #{run.id} «{run.snapshot_name}» от {date:%d.%m.%Y %H:%M}?\n"
        f"Доступно сообщений: {counts['available']}.\nОстальные запуски этой рассылки сохранятся.",
        reply_markup=mailing_run_keyboard(
            run,
            int(history_page),
            int(list_page),
            item.archived_at is not None,
            True,
            confirm=True,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mail:confirm:"))
async def remove_mailing_messages(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
):
    if await deny(callback, settings):
        return
    _, _, run_id, history_page, list_page, _archived = callback.data.split(":")
    await callback.answer("Удаление запущено")
    await clear_inline_keyboard(callback.message)
    service = MailingService(session)
    try:
        pending_run = await service.get_run(int(run_id))
        progress = await callback.message.answer(
            "Удаление выполняется. Можно обновить статус запуска.",
            reply_markup=mailing_run_keyboard(
                pending_run,
                int(history_page),
                int(list_page),
                bool(int(_archived)),
                False,
            ),
        )
        run = await service.delete_messages(
            int(run_id),
            lambda chat_id, message_id: bot.delete_message(chat_id, message_id),
        )
    except ValueError as exc:
        await callback.message.answer(str(exc), reply_markup=admin_menu())
        return
    item = await service.get(run.mailing_id)
    await show_mailing_run(
        progress,
        service,
        run,
        settings,
        int(history_page),
        int(list_page),
        item.archived_at is not None,
    )
