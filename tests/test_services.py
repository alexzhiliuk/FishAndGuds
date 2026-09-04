from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.integrations.iiko.dto import LoyaltyTransaction as IikoLoyaltyTransaction
from app.models import LoyaltyTransaction, NotificationSettings, Purchase, User
from app.repositories import MailingRepository, UserRepository
from app.services import (
    ApplicationSettingsService,
    LoyaltyService,
    MailingService,
    RegistrationService,
    RegistrationSubmission,
    RestaurantService,
    SyncService,
)
from app.services.phone import PhoneNormalizationService
from tests.iiko_double import IikoTestDouble

ORG = "926c9ebc-27a9-4297-a970-a692f1af7f37"


def test_mini_app_registration_submission_validation():
    form = RegistrationSubmission.model_validate(
        {
            "first_name": " Иван ",
            "last_name": " Иванов ",
            "middle_name": "",
            "birthday": "2000-01-02",
            "gender": "male",
            "email": "ivan@example.com",
            "sms_enabled": True,
            "push_enabled": False,
            "email_enabled": True,
            "consent": True,
        }
    )
    assert form.first_name == "Иван"
    assert form.middle_name is None
    assert form.gender == "male"
    assert form.push_enabled is False
    with pytest.raises(ValueError):
        RegistrationSubmission.model_validate(
            {
                "first_name": "Иван",
                "last_name": "Иванов",
                "birthday": "2000-01-02",
                "gender": "female",
                "consent": False,
            }
        )


@pytest.mark.asyncio
async def test_registration_documents_are_stored_and_cleared_in_database(session):
    service = ApplicationSettingsService(session)
    await service.update_document(
        service.PRIVACY_POLICY, "privacy-file-id", "privacy.pdf"
    )
    documents = await service.registration_documents()

    assert documents[service.PRIVACY_POLICY].file_id == "privacy-file-id"
    assert documents[service.PRIVACY_POLICY].file_name == "privacy.pdf"
    assert documents[service.LOYALTY_RULES] is None

    await service.clear_document(service.PRIVACY_POLICY)
    assert await service.get_document(service.PRIVACY_POLICY) is None


def registration(session, client):
    return RegistrationService(
        session, client, default_organization_id=ORG, history_days=365, page_size=100
    )


@pytest.mark.asyncio
async def test_existing_iiko_customer_is_registered_with_balance_and_card(session):
    client = IikoTestDouble(default_organization_id=ORG)
    result = await registration(session, client).start(42, "+375 (29) 123-45-67")
    assert result.user.phone == "+375291234567"
    assert result.user.loyalty_account.card_number
    assert result.user.loyalty_account.last_known_balance == Decimal("300")


@pytest.mark.asyncio
async def test_admin_local_profile_can_be_removed_and_relinked_without_iiko_creation(
    session,
):
    phone = "+375291234567"
    client = IikoTestDouble(default_organization_id=ORG)
    first = await registration(session, client).start(42, phone)
    customer_id = first.user.loyalty_account.iiko_customer_id

    assert await UserRepository(session).delete_by_telegram_id(42) is True
    assert await UserRepository(session).by_telegram_id(42) is None

    second = await registration(session, client).start(42, phone)

    assert second.user.loyalty_account.iiko_customer_id == customer_id
    assert client.create_customer_calls == 0
    assert client.add_card_calls == 0


@pytest.mark.asyncio
async def test_missing_customer_requires_form(session):
    client = IikoTestDouble(
        default_organization_id=ORG, not_found_phones={"+375291111111"}
    )
    result = await registration(session, client).start(42, "+375291111111")
    assert result.needs_form and result.iiko_available


@pytest.mark.asyncio
async def test_new_customer_is_created_in_iiko_with_matching_unique_card(session):
    phone = "+375291111111"
    client = IikoTestDouble(default_organization_id=ORG, not_found_phones={phone})
    user = await registration(session, client).complete(
        telegram_id=42,
        phone=phone,
        first_name="Иван",
        last_name="Иванов",
        middle_name=None,
        birthday=date(2000, 1, 2),
        gender="male",
        email=None,
        sms_enabled=False,
        push_enabled=True,
        email_enabled=False,
        consent=True,
    )
    assert user.gender == "male"
    assert user.notification_settings.sms_enabled is False
    assert user.notification_settings.push_enabled is True
    assert user.notification_settings.email_enabled is False
    assert user.loyalty_account.iiko_customer_id
    assert user.loyalty_account.iiko_sync_status.value == "synced"
    assert user.loyalty_account.last_known_balance == Decimal("0")
    assert user.loyalty_account.card_number.startswith("9898")
    assert len(user.loyalty_account.card_number) == 8
    assert user.loyalty_account.card_track == user.loyalty_account.card_number
    assert client.create_customer_calls == 1
    assert client.add_card_calls == 1


@pytest.mark.asyncio
async def test_card_generation_skips_number_that_already_exists_in_iiko(
    session, monkeypatch
):
    phone = "+375291111111"
    client = IikoTestDouble(default_organization_id=ORG, not_found_phones={phone})
    owner = client.seed_customer("+375299999999")
    owner.cards = [
        owner.cards[0].model_copy(update={"number": "98980001", "track": "98980001"})
    ]
    suffixes = iter((1, 2))
    monkeypatch.setattr("app.services.iiko.randbelow", lambda _: next(suffixes))

    user = await registration(session, client).complete(
        telegram_id=42,
        phone=phone,
        first_name="Иван",
        last_name="Иванов",
        middle_name=None,
        birthday=date(2000, 1, 2),
        gender="male",
        email=None,
        consent=True,
    )

    assert user.loyalty_account.card_number == "98980002"
    assert client.add_card_calls == 1


@pytest.mark.asyncio
async def test_existing_card_does_not_create_another_card(session):
    client = IikoTestDouble(default_organization_id=ORG)
    await registration(session, client).start(42, "+375291234567")
    assert client.add_card_calls == 0


@pytest.mark.asyncio
async def test_iiko_outage_completes_local_registration_with_zero_balance(session):
    client = IikoTestDouble(default_organization_id=ORG, unavailable=True)
    user = await registration(session, client).complete(
        telegram_id=42,
        phone="+375291111111",
        first_name="Иван",
        last_name="Иванов",
        middle_name=None,
        birthday=date(2000, 1, 2),
        email=None,
        consent=True,
    )
    assert user.loyalty_account.iiko_sync_status.value == "pending"
    assert user.loyalty_account.last_known_balance == Decimal("0")


@pytest.mark.asyncio
async def test_retry_pending_registration_searches_then_links(session):
    client = IikoTestDouble(default_organization_id=ORG, unavailable=True)
    user = await registration(session, client).complete(
        telegram_id=42,
        phone="+375291111111",
        first_name="Иван",
        last_name="Иванов",
        middle_name=None,
        birthday=date(2000, 1, 2),
        email=None,
        consent=True,
    )
    client.unavailable = False
    assert await registration(session, client).sync_pending_user(user)
    assert user.loyalty_account.iiko_customer_id


@pytest.mark.asyncio
async def test_repeated_registration_does_not_duplicate_user(session):
    client = IikoTestDouble(default_organization_id=ORG)
    first = await registration(session, client).start(42, "+375291234567")
    second = await registration(session, client).start(42, "+375291234567")
    assert first.user.id == second.user.id
    assert await session.scalar(select(func.count(User.id))) == 1


@pytest.mark.asyncio
async def test_transaction_sync_is_idempotent_and_skips_null_orders(session):
    client = IikoTestDouble(default_organization_id=ORG)
    user = (await registration(session, client).start(42, "+375291234567")).user
    initial_count = await session.scalar(select(func.count(LoyaltyTransaction.id)))
    sync = SyncService(session, client, default_organization_id=ORG)
    await sync.sync_user(user)
    assert (
        await session.scalar(select(func.count(LoyaltyTransaction.id)))
        == initial_count
        == 3
    )
    assert await session.scalar(select(func.count(Purchase.id))) == 1


@pytest.mark.asyncio
async def test_closed_order_is_saved_with_bonus_summary(session):
    class SummaryOnlyIiko(IikoTestDouble):
        def _transactions(self, customer_id: str):
            pos_order_id = f"summary-{self._fingerprint(customer_id)}"
            common = {
                "orderNumber": 33066,
                "orderSum": 22800,
                "posOrderId": pos_order_id,
                "organizationId": self.default_organization_id,
                "isDelivery": False,
                "isIgnored": False,
                "whenCreatedOrder": "2026-08-21T14:10:09Z",
            }
            return [
                IikoLoyaltyTransaction(
                    id=f"earned-{pos_order_id}",
                    revision=1,
                    type=10,
                    typeName="RefillWalletFromOrder",
                    sum=684,
                    balanceBefore=793.1,
                    balanceAfter=1477.1,
                    whenCreated="2026-08-21T16:03:27Z",
                    **common,
                ),
                IikoLoyaltyTransaction(
                    id=f"spent-{pos_order_id}",
                    revision=2,
                    type=8,
                    typeName="PayFromWallet",
                    sum=-100,
                    balanceBefore=893.1,
                    balanceAfter=793.1,
                    whenCreated="2026-08-21T16:03:27Z",
                    **common,
                ),
                IikoLoyaltyTransaction(
                    id=f"closed-{pos_order_id}",
                    revision=3,
                    type=5,
                    typeName="CloseOrder",
                    sum=22800,
                    whenCreated="2026-08-21T16:03:28Z",
                    **common,
                ),
            ]

    client = SummaryOnlyIiko(default_organization_id=ORG)
    await registration(session, client).start(42, "+375291234567")

    purchase = await session.scalar(select(Purchase))
    assert purchase.order_number == "33066"
    assert purchase.amount == Decimal("22800")
    assert purchase.bonus_earned == Decimal("684")
    assert purchase.bonus_spent == Decimal("100")


@pytest.mark.asyncio
async def test_profile_balance_is_cached_wallet_balance_not_transaction_math(session):
    client = IikoTestDouble(default_organization_id=ORG)
    await registration(session, client).start(42, "+375291234567")
    assert (await LoyaltyService(session).get_profile(42))["balance"] == Decimal("300")


@pytest.mark.asyncio
async def test_missing_wallet_is_zero_and_zero_wallet_stays_zero(session):
    client = IikoTestDouble(default_organization_id=ORG)
    client.seed_customer("+375291234567", balance=None)
    user = (await registration(session, client).start(42, "+375291234567")).user
    assert user.loyalty_account.last_known_balance == Decimal("0")
    client.seed_customer("+375291234568", balance=Decimal("0"))
    other = (await registration(session, client).start(43, "+375291234568")).user
    assert other.loyalty_account.last_known_balance == Decimal("0")


@pytest.mark.asyncio
async def test_by_date_pagination_and_cross_organization_duplicates(session):
    client = IikoTestDouble(default_organization_id=ORG)
    service = RegistrationService(
        session, client, default_organization_id=ORG, history_days=365, page_size=1
    )
    await service.start(42, "+375291234567")
    assert await session.scalar(select(func.count(LoyaltyTransaction.id))) == 3


def test_phone_normalization_rejects_malformed_and_collapses_formatting():
    assert PhoneNormalizationService.normalize("8 (999) 123-45-67") == "+79991234567"
    assert PhoneNormalizationService.normalize("+7 999 123 45 67") == "+79991234567"
    with pytest.raises(ValueError):
        PhoneNormalizationService.normalize("123")


@pytest.mark.asyncio
async def test_profile_without_loyalty_account(session):
    session.add(User(telegram_id=7, first_name="No", phone="+10000000000"))
    await session.commit()
    assert await LoyaltyService(session).get_profile(7) is None


@pytest.mark.asyncio
async def test_organization_sync_is_idempotent(session):
    client = IikoTestDouble(default_organization_id=ORG)
    sync = SyncService(session, client, default_organization_id=ORG)
    await sync.sync_restaurants()
    await sync.sync_restaurants()
    from app.models import Restaurant

    assert await session.scalar(select(func.count(Restaurant.id))) == 2


@pytest.mark.asyncio
async def test_iiko_sync_preserves_locally_managed_restaurant_links(session):
    client = IikoTestDouble(default_organization_id=ORG)
    sync = SyncService(session, client, default_organization_id=ORG)
    await sync.sync_restaurants()
    from app.services import RestaurantService

    restaurant = (await RestaurantService(session).list_all())[0]
    await RestaurantService(session).update_local_link(
        restaurant.id, "reviews_url", "https://yandex.by/maps/org/example/reviews/"
    )
    await RestaurantService(session).update_local_link(
        restaurant.id, "booking_url", "https://booking.example.com/fallback"
    )
    await RestaurantService(session).update_local_link(
        restaurant.id, "contact_phone", "+79818977766"
    )

    await sync.sync_restaurants()

    refreshed = await RestaurantService(session).get(restaurant.id)
    assert refreshed.reviews_url == "https://yandex.by/maps/org/example/reviews/"
    assert refreshed.booking_url == "https://booking.example.com/fallback"
    assert refreshed.contact_phone == "+79818977766"
    assert refreshed.website_url in {
        "https://bistro.example.com",
        "https://restaurant.example.com",
    }


@pytest.mark.asyncio
async def test_delivery_can_only_be_configured_for_restaurant(session):
    client = IikoTestDouble(default_organization_id=ORG)
    sync = SyncService(session, client, default_organization_id=ORG)
    await sync.sync_restaurants()
    restaurants = await RestaurantService(session).list_all()
    bistro = next(item for item in restaurants if "Бистро" in item.name)
    restaurant = next(item for item in restaurants if item.name == "Рыба и гады")

    with pytest.raises(ValueError, match="только для ресторана"):
        await RestaurantService(session).update_local_link(
            bistro.id, "delivery_url", "https://delivery.example.com/bistro"
        )

    await RestaurantService(session).update_local_link(
        restaurant.id, "delivery_url", "https://eda.yandex.ru/restaurant/fish"
    )
    assert restaurant.delivery_url == "https://eda.yandex.ru/restaurant/fish"
    assert (
        await RestaurantService(session).delivery_url()
        == "https://eda.yandex.ru/restaurant/fish"
    )


@pytest.mark.asyncio
async def test_missing_iiko_website_clears_it_and_keeps_admin_booking_fallback(session):
    from app.integrations.iiko.dto import IikoOrganization
    from app.repositories.core import RestaurantRepository

    repository = RestaurantRepository(session)
    item = await repository.upsert_organization(
        IikoOrganization(
            id="restaurant-with-booking",
            name="Ресторан",
            website_url="https://iiko.example.com/booking",
        )
    )
    item.booking_url = "https://admin.example.com/booking"
    await session.commit()

    await repository.upsert_organization(
        IikoOrganization(id="restaurant-with-booking", name="Ресторан")
    )
    await session.commit()

    refreshed = await repository.by_iiko_id("restaurant-with-booking")
    assert refreshed.website_url is None
    assert refreshed.booking_url == "https://admin.example.com/booking"


@pytest.mark.asyncio
async def test_mailing_counts_failures_and_excludes_admin(session):
    session.add_all(
        [
            User(telegram_id=1, first_name="A", phone="+10000000001"),
            User(telegram_id=2, first_name="B", phone="+10000000002"),
            User(telegram_id=3, first_name="C", phone="+10000000003"),
        ]
    )
    await session.commit()
    service = MailingService(session)
    item = await service.create("Promo", "Hello")

    async def sender(uid, text, image):
        if uid == 3:
            raise RuntimeError("blocked")

    run = await service.send(item.id, sender, excluded_telegram_ids=(1,))
    assert (run.total_count, run.sent_count, run.failed_count) == (2, 1, 1)


@pytest.mark.asyncio
async def test_telegram_mailing_respects_push_preference(session):
    session.add_all(
        [
            User(
                telegram_id=10,
                first_name="Push",
                phone="+10000000010",
                notification_settings=NotificationSettings(push_enabled=True),
            ),
            User(
                telegram_id=11,
                first_name="No push",
                phone="+10000000011",
                notification_settings=NotificationSettings(push_enabled=False),
            ),
        ]
    )
    await session.commit()
    item = await MailingService(session).create("Promo", "Hello")
    recipients = []

    async def sender(uid, text, image):
        recipients.append(uid)

    run = await MailingService(session).send(item.id, sender)
    assert recipients == [10]
    assert run.total_count == 1


@pytest.mark.asyncio
async def test_sent_mailing_edit_resets_to_draft_and_delete_works(session):
    service = MailingService(session)
    item = await service.create("Old", "Original")

    async def sender(uid, text, image):
        return None

    await service.send(item.id, sender)
    assert (await service.update(item.id, text="Updated")).status.value == "draft"
    await service.delete(item.id)
    assert await MailingRepository(session).get(item.id) is None
