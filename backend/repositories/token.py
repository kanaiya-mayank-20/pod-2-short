"""Refresh token storage: PK ``USER#{userId}`` / SK ``TOKEN#{deviceId}``.

One item per device. Logging in again on the same device replaces the previous token,
and deleting the item revokes it, so a token is never accepted after logout. The
``ttl`` attribute lets DynamoDB delete the item once the token has expired anyway.
"""

from typing import Any

from db.dynamodb import Table
from models import EntityType, RefreshToken, to_iso
from repositories.user import USER_PREFIX


def _drop_empty(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if value is not None}


def _key(user_id: str, device_id: str) -> dict[str, str]:
    return {"PK": f"{USER_PREFIX}{user_id}", "SK": f"TOKEN#{device_id}"}


def _to_item(token: RefreshToken) -> dict[str, Any]:
    return _drop_empty(
        {
            **_key(token.user_id, token.device_id),
            "entity": EntityType.REFRESH_TOKEN.value,
            "user_id": token.user_id,
            "device_id": token.device_id,
            "token_str": token.token_str,
            "expires_at": to_iso(token.expires_at),
            "ip_address": token.ip_address,
            "created_at": to_iso(token.created_at),
            "ttl": token.ttl,
        }
    )


def _from_item(item: dict[str, Any]) -> RefreshToken:
    return RefreshToken.model_validate(item)


class RefreshTokenRepository:
    def __init__(self, table: Table) -> None:
        self.table = table

    async def save(self, token: RefreshToken) -> None:
        """Store (or replace) the token of one device."""
        await self.table.put_item(Item=_to_item(token))

    async def get(self, user_id: str, device_id: str) -> RefreshToken | None:
        resp = await self.table.get_item(Key=_key(user_id, device_id), ConsistentRead=True)
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def delete(self, user_id: str, device_id: str) -> None:
        await self.table.delete_item(Key=_key(user_id, device_id))
