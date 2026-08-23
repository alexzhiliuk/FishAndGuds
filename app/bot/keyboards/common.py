from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def phone_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)


def registration_skip(field: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data=f"registration:skip:{field}")]])


def registration_consent():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Согласен", callback_data="registration:consent:yes")], [InlineKeyboardButton(text="❌ Не согласен", callback_data="registration:consent:no")]])


def registration_confirm():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Всё верно", callback_data="registration:confirm")], [InlineKeyboardButton(text="✏️ Изменить", callback_data="registration:edit")]])


def main_menu(is_admin: bool = False):
    rows = [
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu:profile")],
        [
            InlineKeyboardButton(text="🍽 Забронировать", callback_data="menu:booking"),
            InlineKeyboardButton(text="🛵 Заказать доставку", callback_data="menu:delivery"),
        ],
        [
            InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data="menu:reviews"),
            InlineKeyboardButton(text="📞 Связаться с рестораном", callback_data="menu:contact"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▣ Показать QR", callback_data="profile:qr"), InlineKeyboardButton(text="🧾 История покупок", callback_data="purchases:0")], [InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications:show")], [InlineKeyboardButton(text="📜 Условия программы", callback_data="profile:terms")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:main")]])


def notifications_keyboard(settings):
    mark=lambda value: "✅" if value else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"Акции {mark(settings.promotions_enabled)}", callback_data="notify:promotions")], [InlineKeyboardButton(text=f"Новости {mark(settings.news_enabled)}", callback_data="notify:news")], [InlineKeyboardButton(text=f"Праздники {mark(settings.holidays_enabled)}", callback_data="notify:holidays")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:profile")]])


def restaurant_keyboard(restaurants, action: str):
    rows = [[InlineKeyboardButton(text=r.name, callback_data=f"restaurant:{action}:{r.id}")] for r in restaurants]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def action_keyboard(label: str, url: str | None, action: str):
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text=label, url=url)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"menu:{action}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]])


def mailing_input_back(callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]])


def purchases_keyboard(page: int, has_next: bool):
    row=[]
    if page: row.append(InlineKeyboardButton(text="←", callback_data=f"purchases:{page-1}"))
    if has_next: row.append(InlineKeyboardButton(text="→", callback_data=f"purchases:{page+1}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="purchases:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Создать рассылку", callback_data="admin:create")], [InlineKeyboardButton(text="📨 Рассылки", callback_data="admin:list")], [InlineKeyboardButton(text="🏪 Рестораны и ссылки", callback_data="admin:restaurants")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:main")]])


def admin_restaurants_keyboard(items):
    rows = [[InlineKeyboardButton(text=item.name[:55], callback_data=f"admin:restaurant:{item.id}")] for item in items]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_restaurant_links_keyboard(item_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛵 Доставка", callback_data=f"restaurant_link:delivery_url:{item_id}")],
        [InlineKeyboardButton(text="⭐ Отзывы / Яндекс Карты", callback_data=f"restaurant_link:reviews_url:{item_id}")],
        [InlineKeyboardButton(text="⬅️ К ресторанам", callback_data="admin:restaurants")],
    ])


def mailing_list_keyboard(items, page: int, has_next: bool):
    rows = [[InlineKeyboardButton(text=item.name[:55], callback_data=f"mail:open:{item.id}:{page}")] for item in items]
    pagination = []
    if page > 0: pagination.append(InlineKeyboardButton(text="←", callback_data=f"admin:list:{page - 1}"))
    if has_next: pagination.append(InlineKeyboardButton(text="→", callback_data=f"admin:list:{page + 1}"))
    if pagination: rows.append(pagination)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mailing_actions(item_id: int, status: str, list_page: int = 0):
    rows=[]
    if status != "sending":
        rows += [[InlineKeyboardButton(text="✏️ Название", callback_data=f"mail:edit_name:{item_id}:{list_page}"), InlineKeyboardButton(text="✏️ Текст", callback_data=f"mail:edit_text:{item_id}:{list_page}")], [InlineKeyboardButton(text="🖼 Изображение", callback_data=f"mail:edit_image:{item_id}:{list_page}")], [InlineKeyboardButton(text="🚀 Отправить сейчас", callback_data=f"mail:send:{item_id}"), InlineKeyboardButton(text="🕒 Запланировать", callback_data=f"mail:schedule:{item_id}:{list_page}")]]
    if status == "scheduled": rows.append([InlineKeyboardButton(text="⛔ Отменить", callback_data=f"mail:cancel:{item_id}")])
    if status != "sending": rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mail:delete:{item_id}:{list_page}")])
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=f"admin:list:{list_page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
