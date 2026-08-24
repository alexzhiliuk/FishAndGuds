from datetime import datetime
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import (admin_legal_links_keyboard, admin_menu, admin_restaurant_links_keyboard, admin_restaurants_keyboard,
                                      back_keyboard, mailing_actions, mailing_input_back, mailing_list_keyboard)
from app.bot.media import resolve_mailing_photo
from app.bot.states import ApplicationLinkEdit, MailingCreate, MailingEdit, MailingSchedule, RestaurantLinkEdit
from app.config import Settings
from app.services import ApplicationSettingsService, MailingService, RestaurantService

router = Router(name="admin")
logger = logging.getLogger(__name__)
RESTAURANT_LINK_FIELDS = {
    "delivery_url": "🛵 Доставка",
    "reviews_url": "⭐ Отзывы / Яндекс Карты",
}
APPLICATION_LINK_FIELDS = {
    ApplicationSettingsService.PRIVACY_POLICY_URL: "🔒 Политика обработки персональных данных",
    ApplicationSettingsService.LOYALTY_RULES_URL: "📜 Правила программы лояльности",
}


def allowed(user_id: int, settings: Settings) -> bool: return user_id in settings.admin_ids


async def deny(event, settings):
    if allowed(event.from_user.id, settings): return False
    if isinstance(event, CallbackQuery): await event.answer("Доступ запрещён", show_alert=True)
    else: await event.answer("Доступ запрещён")
    return True


async def send_mailing_preview(message: Message, item, settings: Settings, list_page: int = 0):
    """Render a mailing exactly as a recipient will see it, plus admin actions."""
    reply_markup = mailing_actions(item.id, item.status.value, list_page)
    photo = resolve_mailing_photo(item.image_file_id, settings.assets_dir)
    if photo:
        await message.answer_photo(photo, caption=item.text, reply_markup=reply_markup)
    else:
        await message.answer(item.text, reply_markup=reply_markup)


async def safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).lower():
            raise
        logger.info("Telegram message was already deleted chat_id=%s message_id=%s", message.chat.id, message.message_id)


@router.message(F.text == "⚙️ Админ-панель")
async def open_admin(message: Message, settings: Settings):
    if await deny(message,settings): return
    await message.answer("⚙️ Административное меню",reply_markup=admin_menu())


@router.callback_query(F.data == "menu:admin")
async def open_admin_callback(callback: CallbackQuery, settings: Settings):
    if await deny(callback,settings): return
    await callback.message.answer("⚙️ Административное меню",reply_markup=admin_menu())
    await callback.answer()


def legal_admin_text(values: dict[str, str | None]) -> str:
    lines = ["📄 Документы регистрации", ""]
    for key, label in APPLICATION_LINK_FIELDS.items():
        lines.append(f"{label}:\n{values.get(key) or 'не задана'}\n")
    lines.append("Ссылки открываются в анкете Mini App и хранятся в нашей БД.")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:legal")
async def admin_legal(callback: CallbackQuery, session: AsyncSession, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    await callback.answer(); await state.clear()
    values = await ApplicationSettingsService(session).registration_links()
    await callback.message.edit_text(legal_admin_text(values), reply_markup=admin_legal_links_keyboard())


@router.callback_query(F.data.startswith("legal_link:"))
async def legal_link_start(callback: CallbackQuery, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    key = callback.data.split(":", 1)[1]
    if key not in APPLICATION_LINK_FIELDS:
        await callback.answer("Неизвестная настройка", show_alert=True); return
    await callback.answer(); await state.update_data(application_link_key=key); await state.set_state(ApplicationLinkEdit.value)
    await callback.message.answer(
        f"Отправьте HTTPS-ссылку для раздела «{APPLICATION_LINK_FIELDS[key]}».\n\n"
        "Чтобы очистить поле, напишите «удалить».",
        reply_markup=back_keyboard("admin:legal"),
    )


@router.message(ApplicationLinkEdit.value)
async def legal_link_value(message: Message, state: FSMContext, session: AsyncSession):
    raw_value = (message.text or "").strip()
    if raw_value.lower() in {"удалить", "очистить", "нет", "-"}:
        value = None
    else:
        parsed = urlparse(raw_value)
        if len(raw_value) > 1000 or parsed.scheme != "https" or not parsed.netloc:
            await message.answer("Некорректная ссылка. Отправьте полный HTTPS-адрес или напишите «удалить».", reply_markup=back_keyboard("admin:legal"))
            return
        value = raw_value
    data = await state.get_data()
    await ApplicationSettingsService(session).update_link(data["application_link_key"], value)
    values = await ApplicationSettingsService(session).registration_links()
    await state.clear()
    await message.answer("Ссылка сохранена.\n\n" + legal_admin_text(values), reply_markup=admin_legal_links_keyboard())


def restaurant_admin_text(item) -> str:
    lines = [f"🏪 {item.name}", item.address or "Адрес не указан", "", f"🌐 Сайт из iiko: {item.website_url or 'не получен'}", "", "Локальные ссылки:"]
    for field, label in RESTAURANT_LINK_FIELDS.items():
        lines.append(f"{label}: {getattr(item, field) or 'не задана'}")
    lines.append("\nЭти ссылки хранятся в нашей БД и не перезаписываются из iiko.")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:restaurants")
async def admin_restaurants(callback: CallbackQuery, session: AsyncSession, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    await callback.answer()
    await state.clear()
    items = await RestaurantService(session).list_all()
    text = "🏪 Выберите ресторан:" if items else "Ресторанов в базе пока нет."
    await callback.message.edit_text(text, reply_markup=admin_restaurants_keyboard(items))


@router.callback_query(F.data.startswith("admin:restaurant:"))
async def admin_restaurant(callback: CallbackQuery, session: AsyncSession, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    await callback.answer()
    await state.clear()
    item_id = int(callback.data.rsplit(":", 1)[1])
    item = await RestaurantService(session).get(item_id)
    if item is None:
        await callback.message.edit_text("Ресторан не найден.", reply_markup=back_keyboard("admin:restaurants")); return
    await callback.message.edit_text(restaurant_admin_text(item), reply_markup=admin_restaurant_links_keyboard(item.id))


@router.callback_query(F.data.startswith("restaurant_link:"))
async def restaurant_link_start(callback: CallbackQuery, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    _, field, item_id = callback.data.split(":")
    if field not in RESTAURANT_LINK_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True); return
    await callback.answer()
    await state.update_data(restaurant_id=int(item_id), restaurant_link_field=field)
    await state.set_state(RestaurantLinkEdit.value)
    await callback.message.answer(
        f"Отправьте ссылку для раздела «{RESTAURANT_LINK_FIELDS[field]}».\n\n"
        "Разрешены ссылки http:// и https://. Чтобы очистить поле, напишите «удалить».",
        reply_markup=back_keyboard(f"admin:restaurant:{item_id}"),
    )


@router.message(RestaurantLinkEdit.value)
async def restaurant_link_value(message: Message, state: FSMContext, session: AsyncSession):
    raw_value = (message.text or "").strip()
    if raw_value.lower() in {"удалить", "очистить", "нет", "-"}:
        value = None
    else:
        parsed = urlparse(raw_value)
        if len(raw_value) > 500 or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            data = await state.get_data()
            await message.answer(
                "Некорректная ссылка. Отправьте полный адрес, начинающийся с https://, или напишите «удалить».",
                reply_markup=back_keyboard(f"admin:restaurant:{data['restaurant_id']}"),
            )
            return
        value = raw_value
    data = await state.get_data()
    item = await RestaurantService(session).update_local_link(data["restaurant_id"], data["restaurant_link_field"], value)
    await state.clear()
    await message.answer("Ссылка сохранена.\n\n" + restaurant_admin_text(item), reply_markup=admin_restaurant_links_keyboard(item.id))


@router.callback_query(F.data == "admin:create")
async def create_start(callback: CallbackQuery,state:FSMContext,settings:Settings):
    if await deny(callback,settings): return
    await state.set_state(MailingCreate.name); await callback.message.answer("Введите название рассылки:", reply_markup=mailing_input_back("mail:create_back")); await callback.answer()


@router.callback_query(F.data == "mail:create_back")
async def create_back(callback: CallbackQuery, state: FSMContext, settings: Settings):
    if await deny(callback, settings): return
    await state.clear()
    await callback.message.edit_text("⚙️ Административное меню", reply_markup=admin_menu())
    await callback.answer()


@router.message(MailingCreate.name)
async def create_name(message:Message,state:FSMContext):
    await state.update_data(name=message.text); await state.set_state(MailingCreate.text); await message.answer("Введите текст рассылки:", reply_markup=mailing_input_back("mail:create_back"))


@router.message(MailingCreate.text)
async def create_text(message:Message,state:FSMContext):
    await state.update_data(text=message.text); await state.set_state(MailingCreate.image); await message.answer("Отправьте изображение или напишите «без фото»:", reply_markup=mailing_input_back("mail:create_back"))


@router.message(MailingCreate.image)
async def create_image(message:Message,state:FSMContext,session:AsyncSession):
    data=await state.get_data(); image=message.photo[-1].file_id if message.photo else None
    if not image and (message.text or "").lower() not in {"без фото","нет","-"}: await message.answer("Отправьте фото или напишите «без фото».", reply_markup=mailing_input_back("mail:create_back")); return
    item=await MailingService(session).create(data["name"],data["text"],image); await state.clear()
    await message.answer(f"Рассылка #{item.id} создана.",reply_markup=mailing_actions(item.id,item.status.value))


async def show_mailing_list(message: Message, session: AsyncSession, page: int):
    page = max(page, 0)
    items, has_next = await MailingService(session).page(page)
    if not items and page > 0:
        page -= 1
        items, has_next = await MailingService(session).page(page)
    text = "📨 Выберите рассылку по названию:" if items else "Рассылок пока нет."
    markup = mailing_list_keyboard(items, page, has_next)
    if getattr(message, "photo", None):
        await message.answer(text, reply_markup=markup)
        await safe_delete_message(message)
    else:
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message to edit not found" not in str(exc).lower():
                raise
            await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "admin:list")
@router.callback_query(F.data.startswith("admin:list:"))
async def list_mailings(callback:CallbackQuery,session:AsyncSession,settings:Settings):
    if await deny(callback,settings): return
    page = int(callback.data.rsplit(":", 1)[1]) if callback.data.count(":") == 2 else 0
    await show_mailing_list(callback.message, session, page)
    await callback.answer()


@router.callback_query(F.data.startswith("mail:open:"))
async def open_mailing(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    if await deny(callback, settings): return
    _, _, item_id, page = callback.data.split(":")
    item = await MailingService(session).get(int(item_id))
    if not item:
        await callback.answer("Рассылка не найдена", show_alert=True); return
    await send_mailing_preview(callback.message, item, settings, int(page))
    await safe_delete_message(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("mail:edit_"))
async def edit_start(callback:CallbackQuery,state:FSMContext,settings:Settings):
    if await deny(callback,settings): return
    _, action, item_id, *page_parts = callback.data.split(":"); field=action.removeprefix("edit_")
    list_page = int(page_parts[0]) if page_parts else 0
    states={"name":MailingEdit.name,"text":MailingEdit.text,"image":MailingEdit.image}
    await state.update_data(mailing_id=int(item_id),field=field,list_page=list_page); await state.set_state(states[field]); await callback.message.answer("Отправьте новое значение:" if field != "image" else "Отправьте новое изображение:", reply_markup=mailing_input_back(f"mail:input_back:{item_id}:{list_page}")); await callback.answer()


@router.callback_query(F.data.startswith("mail:input_back:"))
async def mailing_input_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession, settings: Settings):
    if await deny(callback, settings): return
    _, _, item_id, page = callback.data.split(":")
    await state.clear()
    item = await MailingService(session).get(int(item_id))
    if not item:
        await callback.answer("Рассылка не найдена", show_alert=True); return
    await send_mailing_preview(callback.message, item, settings, int(page))
    await safe_delete_message(callback.message)
    await callback.answer()


@router.message(MailingEdit.name)
@router.message(MailingEdit.text)
@router.message(MailingEdit.image)
async def edit_value(message:Message,state:FSMContext,session:AsyncSession):
    data=await state.get_data(); field=data["field"]
    value=message.photo[-1].file_id if field == "image" and message.photo else message.text
    if not value: await message.answer("Некорректное значение.", reply_markup=mailing_input_back(f"mail:input_back:{data['mailing_id']}:{data.get('list_page', 0)}")); return
    item=await MailingService(session).update(data["mailing_id"],**({"image_file_id":value} if field=="image" else {field:value})); await state.clear()
    await message.answer("Рассылка обновлена.",reply_markup=mailing_actions(item.id,item.status.value,data.get("list_page", 0)))


async def telegram_sender(bot:Bot,settings:Settings,telegram_id:int,text:str,image_file_id:str|None):
    photo = resolve_mailing_photo(image_file_id, settings.assets_dir)
    if photo: await bot.send_photo(telegram_id,photo,caption=text)
    else: await bot.send_message(telegram_id,text)


@router.callback_query(F.data.startswith("mail:send:"))
async def send_now(callback:CallbackQuery,session:AsyncSession,bot:Bot,settings:Settings):
    if await deny(callback,settings): return
    item_id=int(callback.data.rsplit(":",1)[1]); await callback.answer("Рассылка запущена")
    run=await MailingService(session).send(
        item_id,
        lambda uid,text,image:telegram_sender(bot,settings,uid,text,image),
        excluded_telegram_ids=settings.admin_ids,
    )
    await callback.message.answer(f"Рассылка завершена.\n\nПолучателей: {run.total_count}\n✅ Отправлено: {run.sent_count}\n❌ Ошибок: {run.failed_count}")


@router.callback_query(F.data.startswith("mail:schedule:"))
async def schedule_start(callback:CallbackQuery,state:FSMContext,settings:Settings):
    if await deny(callback,settings): return
    _, _, item_id, *page_parts = callback.data.split(":")
    list_page = int(page_parts[0]) if page_parts else 0
    await state.update_data(mailing_id=int(item_id), list_page=list_page); await state.set_state(MailingSchedule.when)
    await callback.message.answer("Введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ:", reply_markup=mailing_input_back(f"mail:input_back:{item_id}:{list_page}")); await callback.answer()


@router.message(MailingSchedule.when)
async def schedule_value(message:Message,state:FSMContext,session:AsyncSession,settings:Settings):
    data=await state.get_data()
    back = mailing_input_back(f"mail:input_back:{data['mailing_id']}:{data.get('list_page', 0)}")
    try: when=datetime.strptime(message.text,"%d.%m.%Y %H:%M").replace(tzinfo=ZoneInfo(settings.timezone))
    except (TypeError, ValueError): await message.answer("Неверный формат. Пример: 20.08.2026 14:30", reply_markup=back); return
    if when <= datetime.now(ZoneInfo(settings.timezone)): await message.answer("Время должно быть в будущем.", reply_markup=back); return
    item=await MailingService(session).schedule(data["mailing_id"],when); await state.clear()
    await message.answer(f"Рассылка запланирована на {when:%d.%m.%Y %H:%M}.",reply_markup=mailing_actions(item.id,item.status.value,data.get("list_page", 0)))


@router.callback_query(F.data.startswith("mail:cancel:"))
async def cancel(callback:CallbackQuery,session:AsyncSession,settings:Settings):
    if await deny(callback,settings): return
    await MailingService(session).cancel(int(callback.data.rsplit(":",1)[1])); await callback.message.answer("Рассылка отменена."); await callback.answer()


@router.callback_query(F.data.startswith("mail:delete:"))
async def delete(callback:CallbackQuery,session:AsyncSession,settings:Settings):
    if await deny(callback,settings): return
    _, _, item_id, *page_parts = callback.data.split(":")
    list_page = int(page_parts[0]) if page_parts else 0
    await callback.answer("Удаляю…")
    try:
        await MailingService(session).delete(int(item_id))
    except ValueError as exc:
        if str(exc) != "Рассылка не найдена":
            raise
    await show_mailing_list(callback.message, session, list_page)
