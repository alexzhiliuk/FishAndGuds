from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def registration_web_app_keyboard(url: str):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету", web_app=WebAppInfo(url=url))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu(is_admin: bool = False):
    rows = [
        [
            InlineKeyboardButton(text="🔳 QR-код", callback_data="menu:qr"),
            InlineKeyboardButton(
                text="👤 Личный кабинет", callback_data="menu:profile"
            ),
        ],
        [
            InlineKeyboardButton(text="🍽 Забронировать", callback_data="menu:booking"),
            InlineKeyboardButton(
                text="🛵 Заказать доставку", callback_data="menu:delivery"
            ),
        ],
        [
            InlineKeyboardButton(
                text="⭐ Оставить отзыв", callback_data="menu:reviews"
            ),
            InlineKeyboardButton(
                text="📞 Связаться с рестораном", callback_data="menu:contact"
            ),
        ],
    ]
    if is_admin:
        rows.append(
            [InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="menu:admin")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔳 Показать QR", callback_data="profile:qr"),
                InlineKeyboardButton(text="🧾 Покупки", callback_data="purchases:0"),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Уведомления", callback_data="notifications:show"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Условия программы", callback_data="profile:terms"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:main")],
        ]
    )


def loyalty_terms_keyboard(rules_url: str | None):
    rows = []
    if rules_url:
        rows.append(
            [InlineKeyboardButton(text="📜 Ознакомиться с условиями", url=rules_url)]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notifications_keyboard(settings):
    def mark(value):
        return "✅" if value else "❌"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Акции {mark(settings.promotions_enabled)}",
                    callback_data="notify:promotions",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Новости {mark(settings.news_enabled)}",
                    callback_data="notify:news",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Праздники {mark(settings.holidays_enabled)}",
                    callback_data="notify:holidays",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"СМС {mark(settings.sms_enabled)}", callback_data="notify:sms"
                ),
                InlineKeyboardButton(
                    text=f"PUSH {mark(settings.push_enabled)}",
                    callback_data="notify:push",
                ),
                InlineKeyboardButton(
                    text=f"E-mail {mark(settings.email_enabled)}",
                    callback_data="notify:email",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:profile")],
        ]
    )


def restaurant_keyboard(restaurants, action: str):
    rows = [
        [
            InlineKeyboardButton(
                text=f"🏪 {r.name}", callback_data=f"restaurant:{action}:{r.id}"
            )
        ]
        for r in restaurants
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def action_keyboard(label: str, url: str | None, action: str):
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text=label, url=url)])
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"restaurants:{action}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(callback_data: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
        ]
    )


def mailing_input_back(callback_data: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
        ]
    )


def purchases_keyboard(page: int, has_next: bool):
    row = []
    if page:
        row.append(
            InlineKeyboardButton(
                text="◀️ Предыдущая", callback_data=f"purchases:{page - 1}"
            )
        )
    if has_next:
        row.append(
            InlineKeyboardButton(
                text="Следующая ▶️", callback_data=f"purchases:{page + 1}"
            )
        )
    rows = [row] if row else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="purchases:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать рассылку", callback_data="admin:create"
                )
            ],
            [InlineKeyboardButton(text="📨 Рассылки", callback_data="admin:list")],
            [
                InlineKeyboardButton(
                    text="🏪 Рестораны и ссылки", callback_data="admin:restaurants"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Политика и правила", callback_data="admin:legal"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:main")],
        ]
    )


def admin_legal_links_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔒 Политика обработки данных",
                    callback_data="legal_link:privacy_policy_url",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Правила программы лояльности",
                    callback_data="legal_link:loyalty_rules_url",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")],
        ]
    )


def admin_restaurants_keyboard(items):
    rows = [
        [
            InlineKeyboardButton(
                text=f"🏪 {item.name[:52]}", callback_data=f"admin:restaurant:{item.id}"
            )
        ]
        for item in items
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_restaurant_links_keyboard(item_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛵 Доставка",
                    callback_data=f"restaurant_link:delivery_url:{item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Отзывы / Яндекс Карты",
                    callback_data=f"restaurant_link:reviews_url:{item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К ресторанам", callback_data="admin:restaurants"
                )
            ],
        ]
    )


def mailing_list_keyboard(items, page: int, has_next: bool):
    rows = [
        [
            InlineKeyboardButton(
                text=f"📨 {item.name[:52]}", callback_data=f"mail:open:{item.id}:{page}"
            )
        ]
        for item in items
    ]
    pagination = []
    if page > 0:
        pagination.append(
            InlineKeyboardButton(
                text="◀️ Предыдущая", callback_data=f"admin:list:{page - 1}"
            )
        )
    if has_next:
        pagination.append(
            InlineKeyboardButton(
                text="Следующая ▶️", callback_data=f"admin:list:{page + 1}"
            )
        )
    if pagination:
        rows.append(pagination)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mailing_actions(item_id: int, status: str, list_page: int = 0):
    rows = []
    if status != "sending":
        rows += [
            [
                InlineKeyboardButton(
                    text="✏️ Название",
                    callback_data=f"mail:edit_name:{item_id}:{list_page}",
                ),
                InlineKeyboardButton(
                    text="✏️ Текст",
                    callback_data=f"mail:edit_text:{item_id}:{list_page}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Изображение",
                    callback_data=f"mail:edit_image:{item_id}:{list_page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Отправить сейчас", callback_data=f"mail:send:{item_id}"
                ),
                InlineKeyboardButton(
                    text="🕒 Запланировать",
                    callback_data=f"mail:schedule:{item_id}:{list_page}",
                ),
            ],
        ]
    if status == "scheduled":
        rows.append(
            [
                InlineKeyboardButton(
                    text="⛔ Отменить", callback_data=f"mail:cancel:{item_id}"
                )
            ]
        )
    if status != "sending":
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"mail:delete:{item_id}:{list_page}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К списку", callback_data=f"admin:list_back:{list_page}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
