import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.dto import CustomerInfo, IikoOrganization, LoyaltyTransaction
from app.integrations.iiko.exceptions import IikoAuthenticationError, IikoRequestError, IikoUnavailableError

logger = logging.getLogger(__name__)


class RealIikoClient(IikoClient):
    def __init__(self, *, base_url: str, api_key: str, app_id: str, client_secret: str, timeout: float = 15.0, transport=None):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        self.api_key, self.app_id, self.client_secret = api_key, app_id, client_secret
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def close(self): await self._http.aclose()

    @staticmethod
    def _jwt_expiry(token: str) -> datetime:
        try:
            payload = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
            return datetime.fromtimestamp(json.loads(base64.urlsafe_b64decode(payload))["exp"], timezone.utc)
        except Exception:
            return datetime.now(timezone.utc) + timedelta(minutes=55)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    async def get_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and self._token_expires_at > now + timedelta(minutes=2): return self._token
        try: response = await self._http.post("/api/v2/access_token", json={"apiKey": self.api_key, "appId": self.app_id, "clientSecret": self.client_secret})
        except httpx.HTTPError as exc: raise IikoUnavailableError("iiko authentication is unavailable") from exc
        data = self._json(response); correlation_id = data.get("correlationId")
        logger.info("iiko endpoint=/api/v2/access_token status=%s correlationId=%s", response.status_code, correlation_id)
        if response.status_code >= 400 or not data.get("token"): raise IikoAuthenticationError(f"iiko authentication failed, status={response.status_code}, correlationId={correlation_id}")
        self._token = data["token"]; self._token_expires_at = self._jwt_expiry(self._token); return self._token

    async def _request(self, endpoint: str, payload: dict[str, Any], *, retry_unauthorized: bool = True) -> dict[str, Any]:
        token = await self.get_access_token()
        try: response = await self._http.post(endpoint, json=payload, headers={"Authorization": f"Bearer {token}"})
        except (httpx.TimeoutException, httpx.NetworkError) as exc: raise IikoUnavailableError(f"iiko endpoint unavailable: {endpoint}") from exc
        data = self._json(response); correlation_id = data.get("correlationId")
        logger.info("iiko endpoint=%s status=%s correlationId=%s organizationId=%s", endpoint, response.status_code, correlation_id, payload.get("organizationId"))
        if response.status_code == 401 and retry_unauthorized:
            self._token = None; self._token_expires_at = None
            return await self._request(endpoint, payload, retry_unauthorized=False)
        if response.status_code in (401, 403): raise IikoAuthenticationError(f"iiko authorization failed, status={response.status_code}, correlationId={correlation_id}")
        if response.status_code >= 500: raise IikoUnavailableError(f"iiko server error, status={response.status_code}, correlationId={correlation_id}")
        if response.status_code >= 400: raise IikoRequestError(f"iiko request failed: {endpoint}", status_code=response.status_code, correlation_id=correlation_id)
        return data

    async def get_organizations(self):
        data = await self._request("/api/1/organizations", {"returnAdditionalInfo": True, "includeDisabled": True})
        return [IikoOrganization.model_validate({**item, "is_active": not item.get("isDisabled", False)}) for item in data.get("organizations", [])]

    async def get_customer_info(self, *, organization_id: str, phone: str | None = None, customer_id: str | None = None):
        payload = {"organizationId": organization_id, "type": "phone" if phone else "id", "phone" if phone else "id": phone or customer_id}
        try: data = await self._request("/api/1/loyalty/iiko/customer/info", payload)
        except IikoRequestError as exc:
            if exc.status_code in (400, 404): return None
            raise
        return CustomerInfo.model_validate(data)

    async def add_card(self, *, customer_id: str, card_track: str, card_number: str, organization_id: str):
        await self._request("/api/1/loyalty/iiko/customer/card/add", {"customerId": customer_id, "cardTrack": card_track, "cardNumber": card_number, "organizationId": organization_id})

    async def get_transactions_by_date(self, *, customer_id: str, date_from: datetime, date_to: datetime, page_number: int, page_size: int, organization_id: str):
        data = await self._request("/api/1/loyalty/iiko/customer/transactions/by_date", {"customerId": customer_id, "dateFrom": date_from.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "dateTo": date_to.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "pageNumber": page_number, "pageSize": page_size, "organizationId": organization_id})
        return [LoyaltyTransaction.model_validate(item) for item in data.get("transactions", [])]

    async def get_transactions_by_revision(self, *, customer_id: str, revision: int, last_transaction_id: str | None, page_size: int, organization_id: str):
        payload = {"customerId": customer_id, "revision": revision, "pageSize": page_size, "organizationId": organization_id}
        if last_transaction_id: payload["lastTransactionId"] = last_transaction_id
        data = await self._request("/api/1/loyalty/iiko/customer/transactions/by_revision", payload)
        return [LoyaltyTransaction.model_validate(item) for item in data.get("transactions", [])], int(data.get("lastRevision", revision)), data.get("lastTransactionId")
