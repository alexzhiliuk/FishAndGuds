from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IikoDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class IikoOrganization(IikoDTO):
    id: str
    name: str = "Ресторан"
    restaurant_address: str | None = Field(None, alias="restaurantAddress")
    latitude: float | None = None
    longitude: float | None = None
    code: str | None = None
    inn: str | None = None
    website_url: str | None = None
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def extract_website(cls, value):
        if not isinstance(value, dict) or value.get("website_url"):
            return value
        additional = value.get("additionalInfo") if isinstance(value.get("additionalInfo"), dict) else {}
        website = next((candidate for candidate in (
            value.get("websiteUrl"), value.get("website"), value.get("webSite"), value.get("siteUrl"),
            additional.get("websiteUrl"), additional.get("website"), additional.get("webSite"), additional.get("siteUrl"),
        ) if candidate), None)
        return {**value, "website_url": website} if website else value


class CustomerCard(IikoDTO):
    id: str | None = None
    track: str | None = None
    number: str | None = None
    valid_to_date: datetime | None = Field(None, alias="validToDate")


class CustomerCategory(IikoDTO):
    id: str | None = None
    name: str
    is_active: bool = Field(True, alias="isActive")


class WalletBalance(IikoDTO):
    id: str | None = None
    name: str | None = None
    type: int | str | None = None
    balance: Decimal = Decimal("0")


class CustomerInfo(IikoDTO):
    id: str
    name: str | None = None
    surname: str | None = None
    middle_name: str | None = Field(None, alias="middleName")
    phone: str | None = None
    birthday: datetime | date | None = None
    email: str | None = None
    cards: list[CustomerCard] = Field(default_factory=list)
    categories: list[CustomerCategory] = Field(default_factory=list)
    wallet_balances: list[WalletBalance] = Field(default_factory=list, alias="walletBalances")
    when_registered: datetime | None = Field(None, alias="whenRegistered")

    def bonus_balance(self) -> Decimal:
        for wallet in self.wallet_balances:
            if wallet.type in (1, "1"):
                return wallet.balance
        return Decimal("0")


class LoyaltyTransaction(IikoDTO):
    id: str
    revision: int
    wallet_id: str | None = Field(None, alias="walletId")
    program_id: str | None = Field(None, alias="programId")
    type: int | str
    type_name: str | None = Field(None, alias="typeName")
    amount: Decimal = Field(Decimal("0"), alias="sum")
    comment: str | None = None
    balance_before: Decimal | None = Field(None, alias="balanceBefore")
    balance_after: Decimal | None = Field(None, alias="balanceAfter")
    order_number: str | None = Field(None, alias="orderNumber")
    order_sum: Decimal | None = Field(None, alias="orderSum")
    pos_order_id: str | None = Field(None, alias="posOrderId")
    organization_id: str | None = Field(None, alias="organizationId")
    terminal_group_id: str | None = Field(None, alias="terminalGroupId")
    is_delivery: bool | None = Field(None, alias="isDelivery")
    is_ignored: bool = Field(False, alias="isIgnored")
    when_created: datetime = Field(alias="whenCreated")
    when_created_order: datetime | None = Field(None, alias="whenCreatedOrder")

    @field_validator("order_number", mode="before")
    @classmethod
    def stringify_order_number(cls, value):
        return str(value) if value is not None else None
