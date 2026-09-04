from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from decimal import Decimal

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.bot.handlers import user as user_handlers
from app.bot.handlers import admin as admin_handlers
from app.bot.keyboards.common import (
    action_keyboard,
    admin_legal_links_keyboard,
    admin_menu,
    admin_restaurant_links_keyboard,
    admin_restaurants_keyboard,
    main_menu,
    mailing_actions,
    mailing_input_back,
    mailing_list_keyboard,
    loyalty_terms_keyboard,
    phone_keyboard,
    profile_keyboard,
    purchases_keyboard,
    restaurant_keyboard,
)
from app.bot.navigation import clear_inline_keyboard


def callback_data(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.asyncio
async def test_previous_inline_keyboard_is_removed_before_sending_next_screen():
    message = SimpleNamespace(
        reply_markup=object(),
        edit_reply_markup=AsyncMock(),
    )

    await clear_inline_keyboard(message)

    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_purchase_quantity_has_no_insignificant_zeroes():
    assert user_handlers.format_quantity(Decimal("1.000")) == "1"
    assert user_handlers.format_quantity(Decimal("1.500")) == "1.5"


def test_purchase_summary_displays_order_and_bonus_totals_without_items():
    purchase = SimpleNamespace(
        purchased_at=datetime(2026, 8, 21),
        restaurant=SimpleNamespace(name="Рыба и гады"),
        order_number="33066",
        amount=Decimal("22800"),
        bonus_earned=Decimal("684"),
        bonus_spent=Decimal("100"),
    )

    text = user_handlers.format_purchase(purchase)

    assert "Заказ №33066" in text
    assert "Состав заказа" not in text
    assert "Сумма: 22800.00 BYN" in text
    assert "Начислено баллов: 684" in text
    assert "Списано баллов: 100" in text


@pytest.mark.asyncio
async def test_new_screen_does_not_delete_or_edit_previous_photo_message(tmp_path):
    message = SimpleNamespace(
        photo=[object()],
        reply_markup=object(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
        answer=AsyncMock(),
        edit_text=AsyncMock(),
    )

    await user_handlers.send_visual(
        message, tmp_path / "missing.jpg", "Главное меню", reply_markup="keyboard"
    )

    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    message.delete.assert_not_awaited()
    message.answer.assert_awaited_once_with("Главное меню", reply_markup="keyboard")
    message.edit_text.assert_not_awaited()


def test_main_menu_is_inline_and_uses_callback_navigation():
    markup = main_menu(
        is_admin=True,
        delivery_url="https://delivery.example.com",
        reviews_url="https://reviews.example.com",
    )

    assert isinstance(markup, InlineKeyboardMarkup)
    assert callback_data(markup) == [
        "menu:qr",
        "menu:profile",
        "menu:booking",
        "menu:contact",
        "menu:admin",
    ]
    assert [button.callback_data for button in markup.inline_keyboard[0]] == [
        "menu:qr",
        "menu:profile",
    ]
    assert markup.inline_keyboard[1][0].callback_data == "menu:booking"
    assert markup.inline_keyboard[1][1].url == "https://delivery.example.com"
    assert markup.inline_keyboard[2][0].url == "https://reviews.example.com"
    assert markup.inline_keyboard[0][0].text == "🔲 Мой QR-код"
    assert markup.inline_keyboard[1][0].text == "🍽 Забронировать стол"


def test_phone_keyboard_opens_policy_before_requesting_contact():
    markup = phone_keyboard("https://example.com/privacy.pdf")

    assert markup.keyboard[0][0].text == "Политика"
    assert markup.keyboard[0][0].web_app.url == "https://example.com/privacy.pdf"
    assert markup.keyboard[1][0].request_contact is True


@pytest.mark.asyncio
async def test_registration_prompt_includes_consent_and_configured_policy(
    monkeypatch, tmp_path
):
    class FakeApplicationSettingsService:
        PRIVACY_POLICY_URL = "privacy_policy_url"

        def __init__(self, session):
            pass

        async def registration_links(self):
            return {self.PRIVACY_POLICY_URL: "https://example.com/privacy.pdf"}

    send_visual = AsyncMock()
    monkeypatch.setattr(
        user_handlers, "ApplicationSettingsService", FakeApplicationSettingsService
    )
    monkeypatch.setattr(user_handlers, "send_visual", send_visual)
    settings = SimpleNamespace(
        assets_dir=tmp_path,
        privacy_policy_url="https://fallback.example.com/privacy",
    )

    await user_handlers.send_registration_prompt(
        SimpleNamespace(), settings, session=object()
    )

    text = send_visual.await_args.args[2]
    markup = send_visual.await_args.kwargs["reply_markup"]
    assert "Отправляя данные, вы соглашаетесь" in text
    assert markup.keyboard[0][0].text == "Политика"
    assert markup.keyboard[0][0].web_app.url == "https://example.com/privacy.pdf"


@pytest.mark.asyncio
async def test_found_card_is_confirmed_once_with_main_menu(monkeypatch):
    service = SimpleNamespace(
        start=AsyncMock(return_value=SimpleNamespace(user=object()))
    )
    send_main_menu = AsyncMock()
    monkeypatch.setattr(
        user_handlers, "registration_service", lambda session, iiko, settings: service
    )
    monkeypatch.setattr(user_handlers, "send_main_menu", send_main_menu)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        contact=SimpleNamespace(user_id=42, phone_number="+79818977766"),
        answer=AsyncMock(),
    )

    await user_handlers.register(
        message,
        state=SimpleNamespace(),
        session=object(),
        iiko=object(),
        settings=SimpleNamespace(),
    )

    message.answer.assert_not_awaited()
    send_main_menu.assert_awaited_once()
    assert send_main_menu.await_args.kwargs["text"] == user_handlers.CONNECTED_TEXT


@pytest.mark.asyncio
async def test_registered_start_opens_menu_without_intermediate_message(monkeypatch):
    service = SimpleNamespace(get_local_user=AsyncMock(return_value=object()))
    send_main_menu = AsyncMock()
    monkeypatch.setattr(
        user_handlers, "registration_service", lambda session, iiko, settings: service
    )
    monkeypatch.setattr(user_handlers, "send_main_menu", send_main_menu)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    settings = SimpleNamespace()

    await user_handlers.start(message, object(), settings, object())

    message.answer.assert_not_awaited()
    send_main_menu.assert_awaited_once_with(message, 42, settings)


@pytest.mark.asyncio
async def test_contact_button_shows_clickable_restaurant_phone():
    message = SimpleNamespace(answer=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await user_handlers.contact_restaurant(
        callback, SimpleNamespace(contact_phone="+79818977766")
    )

    text = message.answer.await_args.args[0]
    assert 'href="tel:+79818977766"' in text
    assert callback_data(message.answer.await_args.kwargs["reply_markup"]) == [
        "nav:main"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["menu:qr", "profile:qr"])
async def test_qr_is_sent_without_an_intermediate_text_message(monkeypatch, source):
    class FakeLoyaltyService:
        init_args = None

        def __init__(self, *args):
            FakeLoyaltyService.init_args = args

        async def get_profile(self, telegram_id):
            return {
                "card": SimpleNamespace(qr_payload="98981234", card_number="98981234")
            }

        @staticmethod
        def generate_qr(payload):
            return b"png"

    monkeypatch.setattr(user_handlers, "LoyaltyService", FakeLoyaltyService)
    callback = SimpleNamespace(
        data=source,
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            photo=None,
            edit_text=AsyncMock(),
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    session, iiko = object(), object()
    settings = SimpleNamespace(iiko_default_organization_id="organization-id")

    await user_handlers.qr(callback, session=session, settings=settings, iiko=iiko)

    if source == "menu:qr":
        markup = callback.message.answer_photo.await_args.kwargs["reply_markup"]
        assert callback_data(markup) == ["nav:main"]
    else:
        callback.message.edit_text.assert_not_awaited()
        markup = callback.message.answer_photo.await_args.kwargs["reply_markup"]
        assert callback_data(markup) == ["nav:profile"]
    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_not_awaited()
    assert FakeLoyaltyService.init_args == (session, iiko, "organization-id")


def test_restaurant_admin_text_does_not_expose_internal_system_names():
    item = SimpleNamespace(
        name="Рыба и гады",
        address="Минск",
        website_url=None,
        delivery_url=None,
        reviews_url=None,
    )

    text = admin_handlers.restaurant_admin_text(item)

    assert "iiko" not in text.lower()
    assert "бд" not in text.lower()


def test_main_menu_welcome_text_is_shared_and_complete():
    assert user_handlers.MAIN_MENU_TEXT.startswith(
        "<b>Добро пожаловать в клуб гедонистических привилегий"
    )
    assert "Здесь ваше удовольствие превращается в приятные бонусы." in (
        user_handlers.MAIN_MENU_TEXT
    )
    assert "— Узнать информацию по вашей карте лояльности" in (
        user_handlers.MAIN_MENU_TEXT
    )
    assert "— Связаться с рестораном" in user_handlers.MAIN_MENU_TEXT
    assert user_handlers.MAIN_MENU_TEXT.endswith("Море волнуется без вас!")


def test_main_menu_text_does_not_duplicate_keyboard_actions():
    assert "Выберите действие:" not in user_handlers.MAIN_MENU_TEXT
    assert "🔳 QR-код" not in user_handlers.MAIN_MENU_TEXT
    assert "⚙️ Админ-панель" not in user_handlers.MAIN_MENU_TEXT


def test_main_menu_return_uses_visible_title_without_repeating_welcome():
    assert user_handlers.MENU_ONLY_TEXT == "Главное меню"
    assert "Добро пожаловать" not in user_handlers.MENU_ONLY_TEXT


def test_admin_menu_does_not_expose_users_button():
    assert callback_data(admin_menu()) == [
        "admin:create",
        "admin:list",
        "admin:restaurants",
        "admin:legal",
        "nav:main",
    ]


def test_admin_can_edit_registration_document_links():
    assert callback_data(admin_legal_links_keyboard()) == [
        "legal_link:privacy_policy_url",
        "legal_link:loyalty_rules_url",
        "admin:back",
    ]


def test_admin_can_select_restaurant_and_each_local_link():
    items = [SimpleNamespace(id=5, name="Рыба и гады")]

    assert callback_data(admin_restaurants_keyboard(items)) == [
        "admin:restaurant:5",
        "admin:back",
    ]
    assert callback_data(admin_restaurant_links_keyboard(5)) == [
        "restaurant_link:delivery_url:5",
        "restaurant_link:reviews_url:5",
        "admin:restaurants",
    ]


def test_booking_and_contacts_use_same_iiko_website():
    assert user_handlers.ACTIONS["booking"][1] == "website_url"
    assert user_handlers.ACTIONS["contact"][1] == "website_url"


def test_sent_mailing_remains_editable_and_resendable():
    actions = callback_data(mailing_actions(7, "sent"))
    assert actions == [
        "mail:edit_name:7:0",
        "mail:edit_text:7:0",
        "mail:edit_image:7:0",
        "mail:send:7",
        "mail:schedule:7:0",
        "mail:delete:7:0",
        "admin:list_back:0",
    ]


def test_mailing_input_has_back_navigation():
    assert callback_data(mailing_input_back("mail:input_back:7:2")) == [
        "mail:input_back:7:2"
    ]


def test_mailing_edit_actions_preserve_list_page():
    actions = callback_data(mailing_actions(7, "draft", list_page=2))
    assert "mail:edit_name:7:2" in actions
    assert "mail:edit_text:7:2" in actions
    assert "mail:edit_image:7:2" in actions
    assert "mail:schedule:7:2" in actions
    assert "mail:delete:7:2" in actions
    assert "admin:list_back:2" in actions


@pytest.mark.asyncio
async def test_mailing_input_back_clears_state_and_returns_to_preview(monkeypatch):
    item = SimpleNamespace(id=7, status=SimpleNamespace(value="draft"))
    service = SimpleNamespace(get=AsyncMock(return_value=item))
    preview = AsyncMock()
    monkeypatch.setattr(admin_handlers, "MailingService", lambda session: service)
    monkeypatch.setattr(admin_handlers, "send_mailing_preview", preview)
    message = SimpleNamespace(delete=AsyncMock())
    callback = SimpleNamespace(
        data="mail:input_back:7:2",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    settings = SimpleNamespace(
        admin_ids=(42,), booking_url=None, delivery_url=None, reviews_url=None
    )

    await admin_handlers.mailing_input_cancel(callback, state, object(), settings)

    state.clear.assert_awaited_once_with()
    message.delete.assert_not_awaited()
    preview.assert_awaited_once_with(message, item, settings, 2)
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_delete_mailing_returns_to_originating_list_page(monkeypatch):
    service = SimpleNamespace(delete=AsyncMock())
    show_list = AsyncMock()
    monkeypatch.setattr(admin_handlers, "MailingService", lambda session: service)
    monkeypatch.setattr(admin_handlers, "show_mailing_list", show_list)
    message = SimpleNamespace()
    callback = SimpleNamespace(
        data="mail:delete:7:2",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(admin_ids=(42,))
    session = object()

    await admin_handlers.delete(callback, session, settings)

    service.delete.assert_awaited_once_with(7)
    show_list.assert_awaited_once_with(message, session, 2)
    callback.answer.assert_awaited_once_with("Удаляю…")


@pytest.mark.asyncio
async def test_retried_delete_of_missing_mailing_still_returns_to_list(monkeypatch):
    service = SimpleNamespace(
        delete=AsyncMock(side_effect=ValueError("Рассылка не найдена"))
    )
    show_list = AsyncMock()
    monkeypatch.setattr(admin_handlers, "MailingService", lambda session: service)
    monkeypatch.setattr(admin_handlers, "show_mailing_list", show_list)
    message = SimpleNamespace()
    callback = SimpleNamespace(
        data="mail:delete:7:1",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(admin_ids=(42,))
    session = object()

    await admin_handlers.delete(callback, session, settings)

    show_list.assert_awaited_once_with(message, session, 1)


def test_mailing_name_list_has_selection_and_pagination():
    items = [
        SimpleNamespace(id=5, name="Летнее меню"),
        SimpleNamespace(id=6, name="День рождения"),
    ]
    markup = mailing_list_keyboard(items, page=1, has_next=True)
    assert callback_data(markup) == [
        "mail:open:5:1",
        "mail:open:6:1",
        "admin:list:0",
        "admin:list:2",
        "admin:back",
    ]


def test_profile_has_no_user_notification_settings():
    profile = profile_keyboard()
    assert "profile:main" in callback_data(profile)
    assert any(
        button.text == "🧾 История покупок"
        for row in profile.inline_keyboard
        for button in row
    )
    assert "notifications:show" not in callback_data(profile)
    assert all(
        "Уведомления" not in button.text
        for row in profile.inline_keyboard
        for button in row
    )
    assert "purchases:back" in callback_data(purchases_keyboard(page=0, has_next=False))


def test_loyalty_terms_keyboard_uses_admin_configured_rules_url_only():
    markup = loyalty_terms_keyboard("https://example.com/loyalty-rules")

    assert markup.inline_keyboard[0][0].url == "https://example.com/loyalty-rules"
    assert callback_data(markup) == ["nav:profile"]


@pytest.mark.asyncio
async def test_profile_terms_explains_that_rules_are_available_by_link(monkeypatch):
    class FakeApplicationSettingsService:
        PRIVACY_POLICY_URL = "privacy_policy_url"
        LOYALTY_RULES_URL = "loyalty_rules_url"

        def __init__(self, session):
            pass

        async def registration_links(self):
            return {
                self.PRIVACY_POLICY_URL: "https://example.com/privacy",
                self.LOYALTY_RULES_URL: "https://example.com/rules",
            }

    monkeypatch.setattr(
        user_handlers, "ApplicationSettingsService", FakeApplicationSettingsService
    )
    callback = SimpleNamespace(
        message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock()
    )

    await user_handlers.terms(callback, session=object())

    text = callback.message.answer.await_args.args[0]
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert "Ознакомиться с условиями программы лояльности можно по ссылке ниже" in text
    assert "https://example.com/rules" in text
    assert markup.inline_keyboard[0][0].url == "https://example.com/rules"
    assert all(
        button.url != "https://example.com/privacy"
        for row in markup.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_profile_back_sends_main_menu_as_new_message():
    message = SimpleNamespace(photo=None, edit_text=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(
        admin_ids=(), booking_url=None, delivery_url=None, reviews_url=None
    )

    await user_handlers.profile_to_main(callback, settings)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == user_handlers.MENU_ONLY_TEXT
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_return_from_intermediate_screen_sends_new_message(monkeypatch):
    send_profile = AsyncMock()
    monkeypatch.setattr(user_handlers, "send_profile", send_profile)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42), message=SimpleNamespace(), answer=AsyncMock()
    )
    session, settings, iiko = object(), object(), object()

    await user_handlers.profile_back(callback, session, settings, iiko)

    send_profile.assert_awaited_once_with(callback.message, 42, session, settings, iiko)


@pytest.mark.asyncio
async def test_restaurant_navigation_always_sends_new_messages(monkeypatch):
    restaurants = [
        SimpleNamespace(id=7, name="Ресторан", address=None, website_url=None)
    ]
    service = SimpleNamespace(list_active=AsyncMock(return_value=restaurants))
    monkeypatch.setattr(user_handlers, "RestaurantService", lambda session: service)
    message = SimpleNamespace(photo=None, edit_text=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(data="menu:booking", message=message, answer=AsyncMock())
    settings = SimpleNamespace(
        booking_restaurant_url="https://booking.example.com/restaurant",
        booking_bistro_url="https://booking.example.com/bistro",
        booking_url="https://booking.example.com",
    )

    await user_handlers.choose_restaurant_callback(callback, object(), settings)

    message.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()

    message.edit_text.reset_mock()
    message.answer.reset_mock()
    callback.data = "restaurants:booking"
    await user_handlers.restaurants_back(callback, object(), settings)

    message.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_booking_restaurants_are_parsed_and_open_direct_location_urls(
    monkeypatch,
):
    restaurants = [
        SimpleNamespace(
            id=1,
            name="Бистро Рыба и гады",
            address="Итальянская, 14/16",
            website_url=None,
        ),
        SimpleNamespace(
            id=2,
            name="Рыба и гады",
            address="Большая Конюшенная, 5 Ресторан",
            website_url=None,
        ),
    ]
    service = SimpleNamespace(list_active=AsyncMock(return_value=restaurants))
    monkeypatch.setattr(user_handlers, "RestaurantService", lambda session: service)
    message = SimpleNamespace(answer=AsyncMock())
    settings = SimpleNamespace(
        booking_restaurant_url="https://booking.example.com/restaurant",
        booking_bistro_url="https://booking.example.com/bistro",
        booking_url="https://booking.example.com",
    )

    await user_handlers.send_restaurants(message, object(), "booking", settings)

    service.list_active.assert_awaited_once_with()
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert [row[0].url for row in markup.inline_keyboard[:-1]] == [
        "https://booking.example.com/bistro",
        "https://booking.example.com/restaurant",
    ]
    assert callback_data(markup) == ["nav:main"]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("booking", "Где хотите забронировать стол?"),
        ("delivery", "оформить доставку"),
        ("reviews", "оставить отзыв"),
        ("contact", "связаться"),
    ],
)
def test_restaurant_selection_prompt_explains_the_selected_action(action, expected):
    assert expected in user_handlers.RESTAURANT_PROMPTS[action]


@pytest.mark.asyncio
async def test_admin_navigation_always_sends_new_messages():
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42), message=message, answer=AsyncMock()
    )
    settings = SimpleNamespace(
        admin_ids=(42,), booking_url=None, delivery_url=None, reviews_url=None
    )

    await admin_handlers.open_admin_callback(callback, settings)
    message.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()

    message.edit_text.reset_mock()
    message.answer.reset_mock()
    await admin_handlers.admin_back(callback, settings)
    message.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_notification_buttons_are_safe_noops():
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="notifications:show",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )

    await user_handlers.removed_notifications(callback)

    message.answer.assert_not_awaited()
    message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Раздел уведомлений больше недоступен")


@pytest.mark.asyncio
async def test_purchase_back_sends_new_profile(monkeypatch):
    send_profile = AsyncMock()
    monkeypatch.setattr(user_handlers, "send_profile", send_profile)
    callback = SimpleNamespace(
        data="purchases:back",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )
    session, settings = object(), SimpleNamespace()

    await user_handlers.purchases(callback, session, settings)

    send_profile.assert_awaited_once_with(callback.message, 42, session, settings)


@pytest.mark.parametrize("action", ["booking", "delivery", "reviews", "contact"])
def test_restaurant_flows_have_back_buttons(action):
    restaurants = [SimpleNamespace(id=7, name="Ресторан")]

    selection = restaurant_keyboard(restaurants, action)
    details = action_keyboard("Открыть", "https://example.com", action)
    unavailable = action_keyboard("Открыть", None, action)

    assert callback_data(selection) == [f"restaurant:{action}:7", "nav:main"]
    assert callback_data(details) == [f"restaurants:{action}"]
    assert callback_data(unavailable) == [f"restaurants:{action}"]


@pytest.mark.asyncio
async def test_registered_user_unknown_text_returns_main_menu(monkeypatch):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(
        admin_ids=(42,), booking_url=None, delivery_url=None, reviews_url=None
    )
    service = SimpleNamespace(
        get_local_user=AsyncMock(return_value=SimpleNamespace(id=1))
    )
    monkeypatch.setattr(
        user_handlers, "registration_service", lambda session, iiko, settings: service
    )

    await user_handlers.fallback(
        message, session=object(), settings=settings, iiko=object()
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == user_handlers.MENU_ONLY_TEXT
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert "menu:admin" in callback_data(markup)


@pytest.mark.asyncio
async def test_unregistered_user_unknown_text_gets_contact_prompt(monkeypatch):
    message = SimpleNamespace(from_user=SimpleNamespace(id=42))
    settings = SimpleNamespace()
    service = SimpleNamespace(get_local_user=AsyncMock(return_value=None))
    prompt = AsyncMock()
    monkeypatch.setattr(
        user_handlers, "registration_service", lambda session, iiko, settings: service
    )
    monkeypatch.setattr(user_handlers, "send_registration_prompt", prompt)

    session = object()
    await user_handlers.fallback(
        message, session=session, settings=settings, iiko=object()
    )

    prompt.assert_awaited_once_with(message, settings, session)


@pytest.mark.asyncio
async def test_admin_mailing_preview_uses_photo_and_original_text(tmp_path):
    image = tmp_path / "promo.jpg"
    image.write_bytes(b"image")
    message = SimpleNamespace(answer_photo=AsyncMock(), answer=AsyncMock())
    item = SimpleNamespace(
        id=5,
        text="Текст для получателя",
        image_file_id="asset:promo.jpg",
        status=SimpleNamespace(value="draft"),
    )
    settings = SimpleNamespace(assets_dir=tmp_path)

    await admin_handlers.send_mailing_preview(message, item, settings)

    message.answer_photo.assert_awaited_once()
    assert message.answer_photo.await_args.kwargs["caption"] == item.text
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_mailing_list_shows_compact_names_before_preview(monkeypatch):
    item = SimpleNamespace(id=5, name="Новое меню")
    service = SimpleNamespace(page=AsyncMock(return_value=([item], False)))
    monkeypatch.setattr(admin_handlers, "MailingService", lambda session: service)
    message = SimpleNamespace(photo=None, edit_text=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="admin:list",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(admin_ids=(42,))

    await admin_handlers.list_mailings(callback, object(), settings)

    service.page.assert_awaited_once_with(0)
    message.answer.assert_awaited_once()
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert callback_data(markup) == ["mail:open:5:0", "admin:back"]
    message.edit_text.assert_not_awaited()
