"""Register and login, end to end against a fake DynamoDB."""

from datetime import datetime, timedelta

import jwt
import pytest

from core.config import settings
from core.security import TokenType, create_token
from db.dynamodb import Table

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
USER = {"email": "John@Example.com", "password": "StrongPass123", "name": "John"}
NORMALISED_EMAIL = "john@example.com"


async def register(client, **overrides) -> tuple[int, dict]:
    resp = await client.post(REGISTER, json={**USER, **overrides})
    return resp.status_code, resp.json()


async def login(client, **extra) -> tuple[int, dict]:
    form = {
        "username": extra.pop("username", USER["email"]),
        "password": extra.pop("password", USER["password"]),
        **extra,
    }
    resp = await client.post(LOGIN, data=form)
    return resp.status_code, resp.json()


async def test_register_returns_user_without_the_password(client):
    status, body = await register(client)

    assert status == 201, body
    assert set(body) == {"id", "email", "name", "role", "is_active", "created_at"}
    assert "password" not in " ".join(body)
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert body["email"] == NORMALISED_EMAIL
    created_at = datetime.fromisoformat(body["created_at"])
    assert body["id"] and created_at.utcoffset() == timedelta(0)


async def test_password_is_hashed_and_the_email_is_locked(client, table: Table):
    _, body = await register(client)

    profile = (await table.get_item(Key={"PK": f"USER#{body['id']}", "SK": "PROFILE"}))["Item"]
    assert profile["password_hash"] != USER["password"]
    assert profile["password_hash"].startswith("$argon2")
    assert profile["entity"] == "User"
    assert profile["email"] == NORMALISED_EMAIL

    lock = (await table.get_item(Key={"PK": f"EMAIL#{NORMALISED_EMAIL}", "SK": "LOCK"}))["Item"]
    assert lock["user_id"] == body["id"]
    assert lock["entity"] == "EmailLock"


async def test_duplicate_email_conflicts_even_in_another_case(client):
    await register(client)
    status, body = await register(client, email="JOHN@example.com")

    assert status == 409, body
    assert body == {"error": {"code": "conflict", "message": "Email already registered"}}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email"},
        {"password": "short"},
        {"name": ""},
        {"name": "x" * 201},
    ],
)
async def test_register_validates_input(client, payload):
    status, body = await register(client, **payload)

    assert status == 422, body
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]


async def test_login_returns_tokens_and_stores_the_refresh_token(client, table: Table):
    _, user = await register(client)
    status, token = await login(client, username=NORMALISED_EMAIL)

    assert status == 200, token
    assert set(token) == {"access_token", "refresh_token", "token_type"}
    assert token["token_type"] == "bearer"

    access = decode(token["access_token"])
    refresh = decode(token["refresh_token"])
    assert access["sub"] == user["id"] and access["type"] == "access"
    assert refresh["type"] == "refresh"

    item = (await table.get_item(Key={"PK": f"USER#{user['id']}", "SK": "TOKEN#default"}))["Item"]
    assert item["token_str"] == token["refresh_token"]
    assert item["entity"] == "RefreshToken"
    assert item["ttl"] == refresh["exp"], "ttl must be the token's expiry, for DynamoDB TTL"
    assert item["expires_at"].endswith("+00:00")
    assert "ip_address" in item


def decode(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[settings.ALGORITHM],
    )


async def test_login_keeps_one_token_per_device(client, table: Table):
    _, user = await register(client)

    assert (await login(client, device_id="iphone-12"))[0] == 200
    assert (await login(client, device_id="pixel-7"))[0] == 200

    items = (
        await table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={":pk": f"USER#{user['id']}", ":prefix": "TOKEN#"},
        )
    )["Items"]
    assert sorted(item["device_id"] for item in items) == ["iphone-12", "pixel-7"]

    _, second = await login(client, device_id="iphone-12")
    stored = (await table.get_item(Key={"PK": f"USER#{user['id']}", "SK": "TOKEN#iphone-12"}))[
        "Item"
    ]
    assert stored["token_str"] == second["refresh_token"]


async def test_wrong_password_and_unknown_email_look_the_same(client):
    await register(client)

    wrong_password = await login(client, password="WrongPass123")
    unknown_email = await login(client, username="nobody@example.com")

    for status, body in (wrong_password, unknown_email):
        assert status == 401, body
        assert body == {"error": {"code": "unauthorized", "message": "Incorrect email or password"}}


async def test_401_response_advertises_the_bearer_scheme(client):
    resp = await client.post(LOGIN, data={"username": "x@y.com", "password": "WrongPass123"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_inactive_user_cannot_log_in(client, table: Table):
    _, user = await register(client)
    await table.update_item(
        Key={"PK": f"USER#{user['id']}", "SK": "PROFILE"},
        UpdateExpression="SET is_active = :false",
        ExpressionAttributeValues={":false": False},
    )

    status, body = await login(client)
    assert status == 403, body
    assert body["error"]["code"] == "forbidden"


async def test_expired_access_token_is_rejected(client):
    expired = create_token("some-user", TokenType.ACCESS, timedelta(seconds=-1))

    with pytest.raises(jwt.ExpiredSignatureError):
        decode(expired)


async def test_request_id_header_round_trip(client):
    assert (await client.get("/openapi.json")).headers.get("X-Request-ID")

    resp = await client.get("/openapi.json", headers={"X-Request-ID": "trace-me"})
    assert resp.headers["X-Request-ID"] == "trace-me"
