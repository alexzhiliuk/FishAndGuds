import logging
import re
from datetime import datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import (action_keyboard, back_keyboard, main_menu, notifications_keyboard,
                                      phone_keyboard, profile_keyboard, purchases_keyboard, restaurant_keyboard,
                                      registration_confirm, registration_consent, registration_skip)
from app.bot.states import RegistrationForm
from app.config import Settings
from app.integrations.iiko.client import IikoClient
from app.services import LoyaltyService, NotificationService, PurchaseService, RegistrationService, RestaurantService

router = Router(name="user")
logger = logging.getLogger(__name__)
ACTIONS = {"booking": ("Перейти к бронированию", "website_url", "🍽 Бронирование"), "delivery": ("Заказать доставку", "delivery_url", "🛵 Доставка"), "reviews": ("Открыть Яндекс.Карты", "reviews_url", "⭐ Отзывы"), "contact": ("Открыть сайт ресторана", "website_url", "📞 Контакты")}
MAIN_MENU_TEXT = (
    "<b>Добро пожаловать в клуб гедонистических привилегий Рыба и Гады!</b>\n\n"
    "<i>Здесь ваше удовольствие превращается в приятные бонусы. Следите за новостями, "
    "копите баллы и обменивайте их на любимые блюда.</i>\n\n"
    "Ждем в гости! <i>Море волнуется за вас!</i>"
)


def format_quantity(value) -> str:
    """Display database decimals without insignificant trailing zeroes."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def format_purchase(purchase) -> str:
    order_text = f"Заказ №{purchase.order_number}\n" if purchase.order_number else ""
    bonus_lines = []
    if purchase.bonus_earned:
        bonus_lines.append(f"Начислено баллов: {format_quantity(purchase.bonus_earned)}")
    if purchase.bonus_spent:
        bonus_lines.append(f"Списано баллов: {format_quantity(purchase.bonus_spent)}")
    bonus_text = "\n" + "\n".join(bonus_lines) if bonus_lines else ""
    restaurant = purchase.restaurant.name if purchase.restaurant else "Ресторан"
    return f"{purchase.purchased_at:%d.%m.%Y}\n{restaurant}\n{order_text}Сумма: {purchase.amount:.2f} BYN{bonus_text}"


async def send_visual(message: Message, path: Path, caption: str, **kwargs):
    if path.exists(): return await message.answer_photo(FSInputFile(path), caption=caption, **kwargs)
    return await message.answer(caption, **kwargs)


async def edit_content(message: Message, text: str, **kwargs):
    """Replace photo screens with text so previous media never leaks into menus."""
    if getattr(message, "photo", None):
        await message.delete()
        return await message.answer(text, **kwargs)
    return await message.edit_text(text=text, **kwargs)


async def send_main_menu(message: Message, user_id: int, settings: Settings, text: str = MAIN_MENU_TEXT):
    await message.answer(text, reply_markup=main_menu(user_id in settings.admin_ids), parse_mode="HTML")


async def send_registration_prompt(message: Message, settings: Settings):
    await send_visual(
        message,
        settings.assets_dir / "gallery_21.jpeg",
        "Чтобы подключить карту, нажмите «📱 Поделиться номером» ниже.\n\n"
        "Номер необходимо отправить именно кнопкой Telegram, а не вводить текстом.",
        reply_markup=phone_keyboard(),
    )


def registration_service(session, iiko, settings):
    return RegistrationService(session, iiko, default_organization_id=settings.iiko_default_organization_id, history_days=settings.iiko_transaction_history_days, page_size=settings.iiko_transaction_page_size)


async def send_profile(message: Message, user_id: int, session: AsyncSession, settings: Settings, iiko: IikoClient | None = None):
    data = await LoyaltyService(session, iiko, settings.iiko_default_organization_id).get_profile(user_id)
    if not data:
        await send_registration_prompt(message, settings)
        return
    user, card = data["user"], data["card"]
    full_name = " ".join(filter(None, [user.last_name, user.first_name, user.middle_name]))
    card_text = f"№{card.card_number}" if card.card_number else "ещё синхронизируется"
    text=f"👤 Личный кабинет\n\nИмя: {full_name}\nКарта: {card_text}\nБаланс: {data['balance']:.0f} бонусов"
    await message.answer(text, reply_markup=profile_keyboard())


async def send_restaurants(message: Message, session: AsyncSession, action: str):
    items = await RestaurantService(session).list_active()
    await message.answer("Выберите ресторан:", reply_markup=restaurant_keyboard(items, action))


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient):
    user = await registration_service(session, iiko, settings).get_local_user(message.from_user.id)
    if not user:
        await send_registration_prompt(message, settings); return
    await message.answer("Открываю главное меню.", reply_markup=ReplyKeyboardRemove())
    await send_main_menu(message, message.from_user.id, settings)


@router.message(F.contact)
async def register(message: Message, state: FSMContext, session: AsyncSession, iiko: IikoClient, settings: Settings):
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer("Пожалуйста, отправьте именно свой номер кнопкой ниже."); return
    try:
        result = await registration_service(session, iiko, settings).start(message.from_user.id, message.contact.phone_number)
        await message.answer("Спасибо!", reply_markup=ReplyKeyboardRemove())
        if result.user:
            await message.answer("Карта найдена и подключена! Добро пожаловать!")
            await send_main_menu(message, message.from_user.id, settings); return
        await state.clear(); await state.update_data(phone=message.contact.phone_number, iiko_available=result.iiko_available)
        await state.set_state(RegistrationForm.first_name)
        await message.answer("Введите имя:")
    except Exception:
        logger.exception("Registration failed"); await message.answer("Не удалось получить данные. Попробуйте ещё раз позже.")


@router.message(RegistrationForm.first_name)
async def registration_first_name(message: Message, state: FSMContext):
    if not message.text or not message.text.strip(): await message.answer("Введите имя текстом:"); return
    await state.update_data(first_name=message.text.strip()); await state.set_state(RegistrationForm.last_name); await message.answer("Введите фамилию:")


@router.message(RegistrationForm.last_name)
async def registration_last_name(message: Message, state: FSMContext):
    if not message.text or not message.text.strip(): await message.answer("Введите фамилию текстом:"); return
    await state.update_data(last_name=message.text.strip()); await state.set_state(RegistrationForm.middle_name); await message.answer("Введите отчество или пропустите:", reply_markup=registration_skip("middle_name"))


async def ask_birthday(target, state):
    await state.set_state(RegistrationForm.birthday); await target.answer("Введите дату рождения в формате ДД.ММ.ГГГГ:")


@router.message(RegistrationForm.middle_name)
async def registration_middle(message: Message, state: FSMContext):
    await state.update_data(middle_name=(message.text or "").strip() or None); await ask_birthday(message, state)


@router.callback_query(RegistrationForm.middle_name, F.data == "registration:skip:middle_name")
async def registration_middle_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(middle_name=None); await ask_birthday(callback.message, state); await callback.answer()


@router.message(RegistrationForm.birthday)
async def registration_birthday(message: Message, state: FSMContext):
    try:
        value = datetime.strptime((message.text or "").strip(), "%d.%m.%Y").date()
        if value >= datetime.now().date(): raise ValueError
    except ValueError: await message.answer("Некорректная дата. Используйте формат ДД.ММ.ГГГГ:"); return
    await state.update_data(birthday=value.isoformat()); await state.set_state(RegistrationForm.email); await message.answer("Введите email или пропустите:", reply_markup=registration_skip("email"))


async def ask_consent(target, state):
    await state.set_state(RegistrationForm.consent); await target.answer("Для регистрации нужно согласие на обработку персональных данных.", reply_markup=registration_consent())


@router.message(RegistrationForm.email)
async def registration_email(message: Message, state: FSMContext):
    email = (message.text or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): await message.answer("Некорректный email. Введите снова или нажмите «Пропустить»:", reply_markup=registration_skip("email")); return
    await state.update_data(email=email); await ask_consent(message, state)


@router.callback_query(RegistrationForm.email, F.data == "registration:skip:email")
async def registration_email_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(email=None); await ask_consent(callback.message, state); await callback.answer()


@router.callback_query(RegistrationForm.consent, F.data == "registration:consent:no")
async def registration_consent_no(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.answer("Без согласия регистрация невозможна. Для повторной попытки нажмите /start."); await callback.answer()


@router.callback_query(RegistrationForm.consent, F.data == "registration:consent:yes")
async def registration_consent_yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data(); await state.update_data(consent=True); await state.set_state(RegistrationForm.confirm)
    middle = f" {data['middle_name']}" if data.get('middle_name') else ""
    await callback.message.answer(f"Проверьте данные:\n\n{data['last_name']} {data['first_name']}{middle}\nДата рождения: {datetime.fromisoformat(data['birthday']):%d.%m.%Y}\nEmail: {data.get('email') or 'не указан'}\nТелефон: {data['phone']}", reply_markup=registration_confirm()); await callback.answer()


@router.callback_query(RegistrationForm.confirm, F.data == "registration:edit")
async def registration_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RegistrationForm.first_name); await callback.message.answer("Введите имя заново:"); await callback.answer()


@router.callback_query(RegistrationForm.confirm, F.data == "registration:confirm")
async def registration_complete(callback: CallbackQuery, state: FSMContext, session: AsyncSession, iiko: IikoClient, settings: Settings):
    data = await state.get_data()
    user = await registration_service(session, iiko, settings).complete(telegram_id=callback.from_user.id, phone=data['phone'], first_name=data['first_name'], last_name=data['last_name'], middle_name=data.get('middle_name'), birthday=datetime.fromisoformat(data['birthday']).date(), email=data.get('email'), consent=True)
    pending = user.loyalty_account.iiko_sync_status.value != "synced"
    await state.clear(); await callback.message.answer("Регистрация завершена." + (" Данные iiko синхронизируются автоматически." if pending else " Карта подключена.")); await send_main_menu(callback.message, callback.from_user.id, settings); await callback.answer()


@router.message(F.text == "👤 Личный кабинет")
async def profile(message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient):
    await send_profile(message, message.from_user.id, session, settings, iiko)


@router.callback_query(F.data == "menu:profile")
@router.callback_query(F.data == "nav:profile")
async def profile_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings, iiko: IikoClient):
    data = await LoyaltyService(session, iiko, settings.iiko_default_organization_id).get_profile(callback.from_user.id)
    if not data:
        await send_registration_prompt(callback.message, settings)
        await callback.answer()
        return
    user, card = data["user"], data["card"]
    full_name = " ".join(filter(None, [user.last_name, user.first_name, user.middle_name])); card_text = f"№{card.card_number}" if card.card_number else "ещё синхронизируется"
    text = f"👤 Личный кабинет\n\nИмя: {full_name}\nКарта: {card_text}\nБаланс: {data['balance']:.0f} бонусов"
    await edit_content(callback.message, text, reply_markup=profile_keyboard())
    await callback.answer()


@router.callback_query(F.data == "nav:main")
async def main_menu_callback(callback: CallbackQuery, settings: Settings):
    await send_main_menu(callback.message, callback.from_user.id, settings)
    await callback.answer()


@router.callback_query(F.data == "profile:main")
async def profile_to_main(callback: CallbackQuery, settings: Settings):
    await edit_content(
        callback.message,
        MAIN_MENU_TEXT,
        reply_markup=main_menu(callback.from_user.id in settings.admin_ids),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile:qr")
async def qr(callback: CallbackQuery, session: AsyncSession):
    data=await LoyaltyService(session).get_profile(callback.from_user.id)
    if not data: await callback.answer("Карта не найдена", show_alert=True); return
    if not data["card"].qr_payload: await callback.answer("Карта ещё синхронизируется", show_alert=True); return
    content=LoyaltyService.generate_qr(data["card"].qr_payload)
    await callback.message.answer_photo(BufferedInputFile(content, filename="loyalty-qr.png"), caption=f"Карта №{data['card'].card_number}", reply_markup=back_keyboard("nav:profile"))
    await callback.answer()


@router.callback_query(F.data.startswith("purchases:"))
async def purchases(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    page_value = callback.data.split(":", 1)[1]
    if page_value == "back":
        await send_profile(callback.message, callback.from_user.id, session, settings)
        await callback.answer()
        return
    page=int(page_value); result=await PurchaseService(session).page_for_telegram(callback.from_user.id, page)
    if result is None: await callback.answer("Сначала зарегистрируйтесь", show_alert=True); return
    rows, has_next=result
    if not rows: text="История покупок пока пуста."
    else:
        chunks=[]
        for p in rows:
            chunks.append(format_purchase(p))
        text="🧾 История покупок\n\n"+"\n\n".join(chunks)
    await callback.message.answer(text, reply_markup=purchases_keyboard(page,has_next)); await callback.answer()


@router.callback_query(F.data == "profile:terms")
async def terms(callback: CallbackQuery):
    await callback.message.answer("📜 Бонусами можно оплачивать покупки по правилам программы ресторана. Актуальные ограничения и срок действия бонусов уточняйте у администратора.", reply_markup=back_keyboard("nav:profile")); await callback.answer()


@router.callback_query(F.data == "notifications:show")
async def notification_show(callback: CallbackQuery, session: AsyncSession):
    notification_settings=await NotificationService(session).get_settings(callback.from_user.id)
    if not notification_settings: await callback.answer("Сначала зарегистрируйтесь", show_alert=True); return
    await edit_content(callback.message, "🔔 Уведомления\n\nВыберите категорию, чтобы включить или отключить её.", reply_markup=notifications_keyboard(notification_settings)); await callback.answer()


@router.callback_query(F.data.startswith("notify:"))
async def notification_toggle(callback: CallbackQuery, session: AsyncSession):
    settings=await NotificationService(session).toggle(callback.from_user.id,callback.data.split(":")[1])
    await edit_content(callback.message, "🔔 Уведомления\n\nНастройки сохранены. Выберите категорию, чтобы включить или отключить её.", reply_markup=notifications_keyboard(settings)); await callback.answer("Настройки сохранены")


@router.message(F.text.in_({"🍽 Забронировать", "🛵 Заказать доставку", "⭐ Оставить отзыв", "📞 Связаться с рестораном"}))
async def choose_restaurant(message: Message, session: AsyncSession):
    action={"🍽 Забронировать":"booking","🛵 Заказать доставку":"delivery","⭐ Оставить отзыв":"reviews","📞 Связаться с рестораном":"contact"}[message.text]
    await send_restaurants(message, session, action)


@router.callback_query(F.data.in_({"menu:booking", "menu:delivery", "menu:reviews", "menu:contact"}))
async def choose_restaurant_callback(callback: CallbackQuery, session: AsyncSession):
    action = callback.data.split(":", 1)[1]
    await send_restaurants(callback.message, session, action)
    await callback.answer()


@router.callback_query(F.data.startswith("restaurant:"))
async def restaurant_action(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    _,action,item_id=callback.data.split(":"); item=await RestaurantService(session).get(int(item_id)); label,field,title=ACTIONS[action]
    if not item: await callback.answer("Ресторан не найден",show_alert=True); return
    text=f"{title}\n\n{item.name}\n📍 {item.address}"
    url=getattr(item,field)
    if not url: text += "\n\nСсылка временно недоступна."
    await send_visual(callback.message,settings.assets_dir/(item.image_name or "gallery_10.jpeg"),text,reply_markup=action_keyboard(label,url,action)); await callback.answer()


@router.message()
async def fallback(message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient):
    user = await registration_service(session, iiko, settings).get_local_user(message.from_user.id)
    if user:
        await send_main_menu(message, message.from_user.id, settings)
    else:
        await send_registration_prompt(message, settings)
