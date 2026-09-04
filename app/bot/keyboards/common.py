from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


def phone_keyboard(policy_url: str | None = None):
    rows = []
    if policy_url:
        rows.append(
            [KeyboardButton(text="Политика", web_app=WebAppInfo(url=policy_url))]
        )
    rows.append([KeyboardButton(text="📱 Поделиться номером", request_contact=True)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
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


def main_menu(
    is_admin: bool = False,
    *,
    delivery_url: str | None = None,
    reviews_url: str | None = None,
):
    def external_or_callback(text: str, url: str | None, callback_data: str):
        if url:
            return InlineKeyboardButton(text=text, url=url)
        return InlineKeyboardButton(text=text, callback_data=callback_data)

    rows = [
        [
            InlineKeyboardButton(text="🔲 Мой QR-код", callback_data="menu:qr"),
            InlineKeyboardButton(
                text="👤 Личный кабинет", callback_data="menu:profile"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🍽 Забронировать стол", callback_data="menu:booking"
            ),
            external_or_callback("🛵 Заказать доставку", delivery_url, "menu:delivery"),
        ],
        [
            external_or_callback("⭐ Оставить отзыв", reviews_url, "menu:reviews"),
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
                InlineKeyboardButton(
                    text="🧾 История покупок", callback_data="purchases:0"
                ),
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


def restaurant_keyboard(
    restaurants, action: str, urls_by_id: dict[int, str | None] | None = None
):
    urls_by_id = urls_by_id or {}
    rows = [
        [
            InlineKeyboardButton(
                text=f"🏪 {r.name}",
                **(
                    {"url": urls_by_id[r.id]}
                    if urls_by_id.get(r.id)
                    else {"callback_data": f"restaurant:{action}:{r.id}"}
                ),
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
