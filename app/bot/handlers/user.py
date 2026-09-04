import logging
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    Message,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import (
    action_keyboard,
    back_keyboard,
    main_menu,
    phone_keyboard,
    profile_keyboard,
    purchases_keyboard,
    restaurant_keyboard,
    registration_web_app_keyboard,
)
from app.bot.navigation import (
    answer_photo_with_buttons,
    answer_with_buttons,
)
from app.bot.states import RegistrationForm
from app.config import Settings
from app.integrations.iiko.client import IikoClient
from app.services import (
    ApplicationSettingsService,
    LoyaltyService,
    PurchaseService,
    RegistrationService,
    RegistrationSubmission,
    RestaurantService,
)

router = Router(name="user")
logger = logging.getLogger(__name__)
ACTIONS = {
    "booking": ("🍽 Перейти к бронированию", "website_url", "🍽 Бронирование"),
    "delivery": ("🛵 Заказать доставку", "delivery_url", "🛵 Доставка"),
    "reviews": ("⭐ Открыть Яндекс.Карты", "reviews_url", "⭐ Отзывы"),
    "contact": ("📞 Позвонить", "contact_phone", "📞 Контакты"),
}
RESTAURANT_PROMPTS = {
    "booking": "Где хотите забронировать стол?",
    "delivery": "Выберите ресторан, из которого хотите оформить доставку:",
    "reviews": "Выберите ресторан, о котором хотите оставить отзыв:",
    "contact": "Выберите ресторан, с которым хотите связаться:",
}
MAIN_MENU_TEXT = (
    "<b>Добро пожаловать в клуб гедонистических привилегий Рыба и Гады!</b>\n\n"
    "Здесь ваше удовольствие превращается в приятные бонусы. Следите за новостями, "
    "копите баллы и оплачивайте ими любимые блюда.\n\n"
    "Здесь вы можете:\n"
    "— Узнать информацию по вашей карте лояльности\n"
    "— Показать ваш QR-код для списания и начисления бонусов\n"
    "— Забронировать стол в Бистро или ресторане «Рыба и Гады»\n"
    "— Заказать доставку\n"
    "— Оставить отзыв\n"
    "— Связаться с рестораном\n\n"
    "Ждем в гости! Море волнуется без вас!"
)
MENU_ONLY_TEXT = "Главное меню"
CONNECTED_TEXT = "Спасибо, карта найдена и подключена."


def format_quantity(value) -> str:
    """Display database decimals without insignificant trailing zeroes."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def format_purchase(purchase) -> str:
    order_text = f"Заказ №{purchase.order_number}\n" if purchase.order_number else ""
    bonus_lines = []
    if purchase.bonus_earned:
        bonus_lines.append(
            f"Начислено баллов: {format_quantity(purchase.bonus_earned)}"
        )
    if purchase.bonus_spent:
        bonus_lines.append(f"Списано баллов: {format_quantity(purchase.bonus_spent)}")
    bonus_text = "\n" + "\n".join(bonus_lines) if bonus_lines else ""
    restaurant = purchase.restaurant.name if purchase.restaurant else "Ресторан"
    return f"{purchase.purchased_at:%d.%m.%Y}\n{restaurant}\n{order_text}Сумма: {purchase.amount:.2f} BYN{bonus_text}"


async def send_visual(message: Message, path: Path, caption: str, **kwargs):
    reply_markup = kwargs.pop("reply_markup", None)
    if path.exists():
        return await answer_photo_with_buttons(
            message, FSInputFile(path), caption, reply_markup=reply_markup, **kwargs
        )
    return await answer_with_buttons(
        message, caption, reply_markup=reply_markup, **kwargs
    )


async def send_main_menu(
    message: Message,
    user_id: int,
    settings: Settings,
    session: AsyncSession,
    text: str = MAIN_MENU_TEXT,
):
    is_admin = user_id in settings.admin_ids
    delivery_url = await RestaurantService(session).delivery_url()
    await answer_with_buttons(
        message,
        text,
        reply_markup=main_menu(is_admin, delivery_url),
        parse_mode="HTML",
    )


async def send_registration_prompt(
    message: Message, settings: Settings, session: AsyncSession
):
    policy = await ApplicationSettingsService(session).get_document(
        ApplicationSettingsService.PRIVACY_POLICY
    )
    await send_visual(
        message,
        settings.assets_dir / "gallery_21.jpeg",
        MAIN_MENU_TEXT
        + "\n\nЧтобы подключить карту, нажмите «📱 Поделиться номером» ниже.\n\n"
        "Номер необходимо отправить именно кнопкой Telegram, а не вводить текстом.\n\n"
        "Отправляя данные, вы соглашаетесь с Условиями политики "
        "конфиденциальности и обработкой персональных данных.",
        parse_mode="HTML",
    )
    if policy is not None:
        await message.answer_document(
            policy.file_id,
            caption="Политика обработки персональных данных",
            reply_markup=phone_keyboard(),
        )
    else:
        await message.answer(
            "Политика пока не загружена администратором.",
            reply_markup=phone_keyboard(),
        )


def registration_service(session, iiko, settings):
    return RegistrationService(
        session,
        iiko,
        default_organization_id=settings.iiko_default_organization_id,
        history_days=settings.iiko_transaction_history_days,
        page_size=settings.iiko_transaction_page_size,
        card_number_prefix=settings.iiko_card_number_prefix,
        card_number_length=settings.iiko_card_number_length,
        card_generation_attempts=settings.iiko_card_generation_attempts,
    )


async def send_profile(
    message: Message,
    user_id: int,
    session: AsyncSession,
    settings: Settings,
    iiko: IikoClient | None = None,
):
    data = await LoyaltyService(
        session, iiko, settings.iiko_default_organization_id
    ).get_profile(user_id)
    if not data:
        await send_registration_prompt(message, settings, session)
        return
    user, card = data["user"], data["card"]
    full_name = " ".join(
        filter(None, [user.last_name, user.first_name, user.middle_name])
    )
    card_text = f"№{card.card_number}" if card.card_number else "ещё синхронизируется"
    text = f"👤 Личный кабинет\n\nИмя: {full_name}\nКарта: {card_text}\nБаланс: {data['balance']:.0f} бонусов"
    await answer_with_buttons(message, text, reply_markup=profile_keyboard())


def restaurant_action_url(item, action: str) -> str | None:
    if action == "booking":
        return item.website_url or item.booking_url
    if action == "contact":
        return None
    return getattr(item, ACTIONS[action][1])


async def send_restaurants(message: Message, session: AsyncSession, action: str):
    items = await RestaurantService(session).list_active()
    urls_by_id = {item.id: restaurant_action_url(item, action) for item in items}
    if action == "contact":
        items = [item for item in items if item.contact_phone]
    else:
        items = [item for item in items if urls_by_id[item.id]]
    kwargs = {"reply_markup": restaurant_keyboard(items, action, urls_by_id)}
    text = RESTAURANT_PROMPTS[action]
    if not items:
        text += "\n\nДанные пока не настроены."
    await answer_with_buttons(message, text, **kwargs)


@router.message(CommandStart())
async def start(
    message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    user = await registration_service(session, iiko, settings).get_local_user(
        message.from_user.id
    )
    if not user:
        await send_registration_prompt(message, settings, session)
        return
    await send_main_menu(message, message.from_user.id, settings, session)


@router.message(F.text == "Политика")
async def privacy_policy(message: Message, session: AsyncSession):
    document = await ApplicationSettingsService(session).get_document(
        ApplicationSettingsService.PRIVACY_POLICY
    )
    if document is None:
        await message.answer("Политика пока не загружена администратором.")
        return
    await message.answer_document(
        document.file_id,
        caption="Политика обработки персональных данных",
        reply_markup=phone_keyboard(),
    )


@router.message(F.contact)
async def register(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    iiko: IikoClient,
    settings: Settings,
):
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer("Пожалуйста, отправьте именно свой номер кнопкой ниже.")
        return
    try:
        result = await registration_service(session, iiko, settings).start(
            message.from_user.id, message.contact.phone_number
        )
        if result.user:
            await state.clear()
            await send_main_menu(
                message,
                message.from_user.id,
                settings,
                session,
                text=CONNECTED_TEXT,
            )
            return
        state_data = await state.get_data()
        if state_data.get("admin_local_registration_only"):
            await message.answer(
                "Гость с этим номером не найден в iiko. Локальный профиль не создан. "
                "Для тестовой регистрации используйте номер уже существующего гостя iiko."
            )
            return
        await state.clear()
        await state.update_data(
            phone=message.contact.phone_number, iiko_available=result.iiko_available
        )
        await state.set_state(RegistrationForm.mini_app)
        await answer_with_buttons(
            message,
            "Карта не найдена. Заполните короткую анкету:",
            reply_markup=registration_web_app_keyboard(
                settings.registration_web_app_url
            ),
        )
    except Exception:
        logger.exception("Registration failed")
        await message.answer("Не удалось получить данные. Попробуйте ещё раз позже.")


@router.message(RegistrationForm.mini_app, F.web_app_data)
async def registration_complete(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    iiko: IikoClient,
    settings: Settings,
):
    try:
        form = RegistrationSubmission.model_validate_json(message.web_app_data.data)
    except ValidationError:
        await message.answer(
            "Анкета содержит некорректные данные. Откройте её и проверьте обязательные поля."
        )
        return
    data = await state.get_data()
    user = await registration_service(session, iiko, settings).complete(
        telegram_id=message.from_user.id,
        phone=data["phone"],
        first_name=form.first_name,
        last_name=form.last_name,
        middle_name=form.middle_name,
        birthday=form.birthday,
        gender=form.gender,
        email=form.email,
        sms_enabled=form.sms_enabled,
        push_enabled=form.push_enabled,
        email_enabled=form.email_enabled,
        consent=form.consent,
    )
    pending = user.loyalty_account.iiko_sync_status.value != "synced"
    await state.clear()
    await send_main_menu(
        message,
        message.from_user.id,
        settings,
        session,
        text=(
            "Спасибо, регистрация завершена. Карта создаётся автоматически."
            if pending
            else CONNECTED_TEXT
        ),
    )


@router.message(F.text == "👤 Личный кабинет")
async def profile(
    message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    await send_profile(message, message.from_user.id, session, settings, iiko)


@router.callback_query(F.data == "menu:profile")
async def profile_callback(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    data = await LoyaltyService(
        session, iiko, settings.iiko_default_organization_id
    ).get_profile(callback.from_user.id)
    if not data:
        await send_registration_prompt(callback.message, settings, session)
        await callback.answer()
        return
    user, card = data["user"], data["card"]
    full_name = " ".join(
        filter(None, [user.last_name, user.first_name, user.middle_name])
    )
    card_text = f"№{card.card_number}" if card.card_number else "ещё синхронизируется"
    text = f"👤 Личный кабинет\n\nИмя: {full_name}\nКарта: {card_text}\nБаланс: {data['balance']:.0f} бонусов"
    await answer_with_buttons(callback.message, text, reply_markup=profile_keyboard())
    await callback.answer()


@router.callback_query(F.data == "nav:profile")
async def profile_back(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    await send_profile(callback.message, callback.from_user.id, session, settings, iiko)
    await callback.answer()


@router.callback_query(F.data == "nav:main")
async def main_menu_callback(
    callback: CallbackQuery, settings: Settings, session: AsyncSession
):
    await send_main_menu(
        callback.message,
        callback.from_user.id,
        settings,
        session,
        text=MENU_ONLY_TEXT,
    )
    await callback.answer()


@router.callback_query(F.data == "profile:main")
async def profile_to_main(
    callback: CallbackQuery, settings: Settings, session: AsyncSession
):
    await send_main_menu(
        callback.message,
        callback.from_user.id,
        settings,
        session,
        text=MENU_ONLY_TEXT,
    )
    await callback.answer()


@router.callback_query(F.data.in_({"profile:qr", "menu:qr"}))
async def qr(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    data = await LoyaltyService(
        session, iiko, settings.iiko_default_organization_id
    ).get_profile(callback.from_user.id)
    if not data:
        await callback.answer("Карта не найдена", show_alert=True)
        return
    if not data["card"].qr_payload:
        await callback.answer("Карта ещё синхронизируется", show_alert=True)
        return
    content = LoyaltyService.generate_qr(data["card"].qr_payload)
    if callback.data == "menu:qr":
        await answer_photo_with_buttons(
            callback.message,
            BufferedInputFile(content, filename="loyalty-qr.png"),
            f"Карта №{data['card'].card_number}",
            reply_markup=back_keyboard("nav:main"),
        )
    else:
        await answer_photo_with_buttons(
            callback.message,
            BufferedInputFile(content, filename="loyalty-qr.png"),
            f"Карта №{data['card'].card_number}",
            reply_markup=back_keyboard("nav:profile"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("purchases:"))
async def purchases(callback: CallbackQuery, session: AsyncSession, settings: Settings):
    page_value = callback.data.split(":", 1)[1]
    if page_value == "back":
        await send_profile(callback.message, callback.from_user.id, session, settings)
        await callback.answer()
        return
    page = int(page_value)
    result = await PurchaseService(session).page_for_telegram(
        callback.from_user.id, page
    )
    if result is None:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return
    rows, has_next = result
    if not rows:
        text = "История покупок пока пуста."
    else:
        chunks = []
        for p in rows:
            chunks.append(format_purchase(p))
        text = "🧾 История покупок\n\n" + "\n\n".join(chunks)
    await answer_with_buttons(
        callback.message, text, reply_markup=purchases_keyboard(page, has_next)
    )
    await callback.answer()


@router.callback_query(F.data == "profile:terms")
async def terms(callback: CallbackQuery, session: AsyncSession):
    document = await ApplicationSettingsService(session).get_document(
        ApplicationSettingsService.LOYALTY_RULES
    )
    if document is None:
        await answer_with_buttons(
            callback.message,
            "📜 Правила программы лояльности пока не загружены администратором.",
            reply_markup=back_keyboard("nav:profile"),
        )
    else:
        await callback.message.answer_document(
            document.file_id,
            caption="📜 Правила программы лояльности",
            reply_markup=back_keyboard("nav:profile"),
        )
    await callback.answer()


@router.callback_query(F.data == "notifications:show")
@router.callback_query(F.data.startswith("notify:"))
async def removed_notifications(callback: CallbackQuery):
    await callback.answer("Раздел уведомлений больше недоступен")


@router.message(
    F.text.in_(
        {
            "🍽 Забронировать",
            "🛵 Заказать доставку",
            "⭐ Оставить отзыв",
            "📞 Связаться с рестораном",
        }
    )
)
async def choose_restaurant(message: Message, session: AsyncSession):
    action = {
        "🍽 Забронировать": "booking",
        "🛵 Заказать доставку": "delivery",
        "⭐ Оставить отзыв": "reviews",
        "📞 Связаться с рестораном": "contact",
    }[message.text]
    await send_restaurants(message, session, action)


@router.callback_query(
    F.data.in_({"menu:booking", "menu:delivery", "menu:reviews", "menu:contact"})
)
async def choose_restaurant_callback(callback: CallbackQuery, session: AsyncSession):
    action = callback.data.split(":", 1)[1]
    if action == "delivery":
        url = await RestaurantService(session).delivery_url()
        text = "🛵 Доставка"
        if not url:
            text += "\n\nСсылка пока не настроена."
        await answer_with_buttons(
            callback.message,
            text,
            reply_markup=action_keyboard(
                "🛵 Открыть Яндекс Еду", url, "delivery", back_callback="nav:main"
            ),
        )
        await callback.answer()
        return
    await send_restaurants(callback.message, session, action)
    await callback.answer()


@router.callback_query(F.data.startswith("restaurants:"))
async def restaurants_back(callback: CallbackQuery, session: AsyncSession):
    action = callback.data.split(":", 1)[1]
    await send_restaurants(callback.message, session, action)
    await callback.answer()


@router.callback_query(F.data.startswith("restaurant:"))
async def restaurant_action(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
):
    _, action, item_id = callback.data.split(":")
    item = await RestaurantService(session).get(int(item_id))
    label, _, title = ACTIONS[action]
    if not item:
        await callback.answer("Ресторан не найден", show_alert=True)
        return
    if action == "contact":
        if not item.contact_phone:
            await callback.answer("Телефон временно недоступен", show_alert=True)
            return
        phone = escape(item.contact_phone)
        await answer_with_buttons(
            callback.message,
            f'{title}\n\n{escape(item.name)}\n<a href="tel:{phone}">{phone}</a>',
            reply_markup=back_keyboard("restaurants:contact"),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    text = f"{title}\n\n{item.name}\n📍 {item.address}"
    url = restaurant_action_url(item, action)
    if not url:
        text += "\n\nСсылка временно недоступна."
    await send_visual(
        callback.message,
        settings.assets_dir / (item.image_name or "gallery_10.jpeg"),
        text,
        reply_markup=action_keyboard(label, url, action),
    )
    await callback.answer()


@router.message()
async def fallback(
    message: Message, session: AsyncSession, settings: Settings, iiko: IikoClient
):
    user = await registration_service(session, iiko, settings).get_local_user(
        message.from_user.id
    )
    if user:
        await send_main_menu(
            message,
            message.from_user.id,
            settings,
            session,
            text=MENU_ONLY_TEXT,
        )
    else:
        await send_registration_prompt(message, settings, session)
