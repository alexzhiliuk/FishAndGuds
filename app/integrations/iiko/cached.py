from datetime import datetime

from app.cache import RedisJsonCache
from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.dto import CustomerCreate, IikoOrganization


class CachedIikoClient(IikoClient):
    """Caches stable iiko reads while keeping customer balance requests fresh."""

    ORGANIZATIONS_KEY = "iiko:organizations:v1"

    def __init__(
        self, backend: IikoClient, cache: RedisJsonCache, *, organizations_ttl: int
    ):
        self.backend = backend
        self.cache = cache
        self.organizations_ttl = organizations_ttl

    async def get_access_token(self) -> str:
        return await self.backend.get_access_token()

    async def get_organizations(self) -> list[IikoOrganization]:
        cached = await self.cache.get(self.ORGANIZATIONS_KEY)
        if isinstance(cached, list):
            return [IikoOrganization.model_validate(item) for item in cached]
        items = await self.backend.get_organizations()
        await self.cache.set(
            self.ORGANIZATIONS_KEY,
            [item.model_dump(by_alias=True, mode="json") for item in items],
            self.organizations_ttl,
        )
        return items

    async def get_customer_info(
        self,
        *,
        organization_id: str,
        phone: str | None = None,
        customer_id: str | None = None,
        card_number: str | None = None,
    ):
        return await self.backend.get_customer_info(
            organization_id=organization_id,
            phone=phone,
            customer_id=customer_id,
            card_number=card_number,
        )

    async def create_or_update_customer(
        self, *, organization_id: str, customer: CustomerCreate
    ) -> str:
        return await self.backend.create_or_update_customer(
            organization_id=organization_id, customer=customer
        )

    async def add_card(
        self,
        *,
        customer_id: str,
        card_track: str,
        card_number: str,
        organization_id: str,
    ) -> None:
        await self.backend.add_card(
            customer_id=customer_id,
            card_track=card_track,
            card_number=card_number,
            organization_id=organization_id,
        )

    async def get_transactions_by_date(
        self,
        *,
        customer_id: str,
        date_from: datetime,
        date_to: datetime,
        page_number: int,
        page_size: int,
        organization_id: str,
    ):
        return await self.backend.get_transactions_by_date(
            customer_id=customer_id,
            date_from=date_from,
            date_to=date_to,
            page_number=page_number,
            page_size=page_size,
            organization_id=organization_id,
        )

    async def get_transactions_by_revision(
        self,
        *,
        customer_id: str,
        revision: int,
        last_transaction_id: str | None,
        page_size: int,
        organization_id: str,
    ):
        return await self.backend.get_transactions_by_revision(
            customer_id=customer_id,
            revision=revision,
            last_transaction_id=last_transaction_id,
            page_size=page_size,
            organization_id=organization_id,
        )

    async def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close:
            await close()
