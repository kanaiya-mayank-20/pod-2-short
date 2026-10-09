# FastAPI + DynamoDB Production Guide (Industry Standard)

A practical guide to build a **production-ready REST API** with Python, FastAPI and **Amazon DynamoDB**.
Everything is explained in simple words, with example code you can copy.

---

## Table of Contents

1. [What changes when you use DynamoDB?](#1-what-changes-when-you-use-dynamodb)
2. [Recommended tech stack](#2-recommended-tech-stack)
3. [Project folder structure](#3-project-folder-structure)
4. [REST API architecture (layers)](#4-rest-api-architecture-layers)
5. [Configuration and environment variables](#5-configuration-and-environment-variables)
6. [DynamoDB data modeling (most important part)](#6-dynamodb-data-modeling-most-important-part)
7. [Creating the table (Terraform + local script)](#7-creating-the-table-terraform--local-script)
8. [Connecting FastAPI to DynamoDB](#8-connecting-fastapi-to-dynamodb)
9. [Models, schemas, repositories, services](#9-models-schemas-repositories-services)
10. [DynamoDB cheat sheet (conditions, updates, transactions)](#10-dynamodb-cheat-sheet)
11. [Error handling](#11-error-handling)
12. [JWT authentication (full example)](#12-jwt-authentication-full-example)
13. [Role-based access control (RBAC)](#13-role-based-access-control-rbac)
14. [Pagination (cursor based)](#14-pagination-cursor-based)
15. [Rate limiting](#15-rate-limiting)
16. [REST API design rules](#16-rest-api-design-rules)
17. [Logging and request IDs](#17-logging-and-request-ids)
18. [Security best practices (including IAM)](#18-security-best-practices-including-iam)
19. [Caching](#19-caching)
20. [Background jobs and events](#20-background-jobs-and-events)
21. [Health checks and monitoring](#21-health-checks-and-monitoring)
22. [Testing](#22-testing)
23. [Code quality tools](#23-code-quality-tools)
24. [Docker and deployment on AWS](#24-docker-and-deployment-on-aws)
25. [CI/CD pipeline](#25-cicd-pipeline)
26. [Final production checklist](#26-final-production-checklist)

---

## 1. What changes when you use DynamoDB?

DynamoDB is a **NoSQL key-value / document database** managed by AWS. It is very fast (single-digit milliseconds) and scales automatically, but it works **very differently** from PostgreSQL or MySQL.

| SQL (PostgreSQL) | DynamoDB |
|---|---|
| Design tables first, write queries later | **Design your access patterns first**, then design keys |
| `JOIN` across tables | **No joins**. Related data is stored together (same partition) |
| `WHERE` on any column | Query only by **partition key** (+ sort key). Other columns need an **index** |
| `OFFSET / LIMIT` pagination | **Cursor pagination only** (`LastEvaluatedKey`) |
| `COUNT(*)` is cheap | Counting is expensive (no cheap total count) |
| Schema migrations (Alembic) | **Schemaless** items; tables/indexes managed with IaC (Terraform/CDK) |
| Unique constraint on email | Enforce uniqueness yourself (with a **transaction**, see section 9) |
| ORM (SQLAlchemy) | Plain `boto3` / `aioboto3` calls, wrapped in repositories |
| Pay for servers | Pay per request (on-demand) or per provisioned capacity |

**The golden rule:** in DynamoDB you decide *how you will read the data* **before** you decide how to store it. Changing access patterns later is harder than in SQL.

---

## 2. Recommended tech stack

| Purpose | Tool |
|---|---|
| Web framework | **FastAPI** |
| Server | **Uvicorn** (workers managed by **Gunicorn**) |
| Validation / settings | **Pydantic v2** + **pydantic-settings** |
| Database | **Amazon DynamoDB** |
| AWS SDK (async) | **aioboto3** (async version of boto3) |
| Table/infra management | **Terraform** or **AWS CDK** |
| Local development DB | **DynamoDB Local** (Docker) or **LocalStack** |
| Password hashing | **pwdlib[argon2]** |
| JWT | **PyJWT** |
| Cache / rate limit store (optional) | **Redis (ElastiCache)** |
| Rate limiting | **slowapi**, or API Gateway / AWS WAF |
| Background jobs | **SQS + worker**, **DynamoDB Streams + Lambda**, or Celery |
| Testing | **pytest**, **pytest-asyncio**, **httpx**, **moto** |
| Lint / format / types | **Ruff**, **mypy** |
| Package manager | **uv** or **Poetry** (use lock files) |
| Container | **Docker** + ECS Fargate / EKS |
| Monitoring | **CloudWatch**, **Prometheus**, **Sentry**, **OpenTelemetry (X-Ray)** |

Install (example):

```bash
uv init myapp && cd myapp
uv add fastapi "uvicorn[standard]" gunicorn uvicorn-worker \
       aioboto3 \
       pydantic-settings "pydantic[email]" \
       pyjwt "pwdlib[argon2]" python-multipart \
       slowapi redis
uv add --dev pytest pytest-asyncio pytest-cov httpx "moto[server]" ruff mypy pre-commit
```

---

## 3. Project folder structure

The main idea: **separate responsibilities**. Routes handle HTTP only. Services hold business logic. Repositories talk to DynamoDB.

```
myapp/
├── app/
│   ├── __init__.py
│   ├── main.py                  # creates the FastAPI app, middleware, routers
│   │
│   ├── core/                    # app-wide basics (no business logic)
│   │   ├── config.py            # settings from environment variables
│   │   ├── security.py          # password hashing + JWT create/decode
│   │   ├── exceptions.py        # custom exceptions + handlers
│   │   ├── logging.py           # logging setup
│   │   ├── pagination.py        # cursor encode/decode
│   │   └── rate_limit.py        # limiter (Redis) + DynamoDB counter
│   │
│   ├── db/
│   │   ├── dynamodb.py          # aioboto3 resource + get_table dependency
│   │   └── table_schema.py      # table definition (used by scripts & tests)
│   │
│   ├── models/                  # internal domain objects (Pydantic)
│   │   └── user.py
│   │
│   ├── schemas/                 # request/response shapes (Pydantic)
│   │   ├── user.py
│   │   ├── token.py
│   │   └── common.py            # Page, PageParams
│   │
│   ├── repositories/            # ONLY DynamoDB calls
│   │   ├── user.py
│   │   └── token_blocklist.py
│   │
│   ├── services/                # business logic
│   │   ├── auth.py
│   │   └── user.py
│   │
│   ├── api/                     # HTTP layer
│   │   ├── deps.py              # shared dependencies (current user, roles)
│   │   └── v1/
│   │       ├── router.py
│   │       └── endpoints/
│   │           ├── auth.py
│   │           ├── users.py
│   │           └── health.py
│   │
│   └── middleware/
│       └── request_id.py
│
├── infra/                       # Terraform / CDK for table, IAM, etc.
│   └── dynamodb.tf
├── scripts/
│   └── create_table.py          # create table in DynamoDB Local
├── tests/
│   ├── conftest.py
│   └── test_auth.py
│
├── .env.example                 # sample env (commit this)
├── .env                         # local secrets (NEVER commit)
├── .gitignore
├── .pre-commit-config.yaml
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

**What changed compared to a SQL project?**

- No `alembic/` folder and no SQLAlchemy `models/`. The table is created by Terraform (`infra/`).
- `db/dynamodb.py` replaces the SQL engine/session.
- Pagination uses cursors instead of page numbers.

| Folder | Think of it as | Rule |
|---|---|---|
| `api/` | The waiter | Takes the request, calls a service, returns the response. No DynamoDB calls here. |
| `services/` | The chef | Business rules ("email must be unique", "user must be active"). |
| `repositories/` | The storeroom keeper | Only talks to DynamoDB. Knows about keys like `USER#123`. |
| `models/` | Internal data shape | What a user looks like inside the app (includes password hash). |
| `schemas/` | The menu format | What data comes in and goes out of the API. |
| `core/` | Toolbox | Config, security, logging. |

---

## 4. REST API architecture (layers)

```
   Client (Browser / Mobile)
            │  HTTPS
            ▼
   ┌─────────────────────────────┐
   │ CloudFront / WAF / ALB      │  TLS, DDoS protection, coarse rate limit
   └─────────────┬───────────────┘
                 ▼
   ┌─────────────────────────────┐
   │ Middleware                  │  CORS, request-id, logging, rate limit
   └─────────────┬───────────────┘
                 ▼
   ┌─────────────────────────────┐
   │ Router (api/)               │  validates input with Pydantic schema
   └─────────────┬───────────────┘
                 ▼
   ┌─────────────────────────────┐
   │ Dependencies                │  JWT auth, roles, DynamoDB table
   └─────────────┬───────────────┘
                 ▼
   ┌─────────────────────────────┐
   │ Service                     │  business logic
   └─────────────┬───────────────┘
                 ▼
   ┌─────────────────────────────┐
   │ Repository                  │  DynamoDB GetItem / Query / PutItem ...
   └─────────────┬───────────────┘
                 ▼
        DynamoDB   (+ Redis / SQS optional)
```

**Why layers?** If your access patterns or table design change, you only change the repository. Services and endpoints stay the same.

---

## 5. Configuration and environment variables

**`app/core/config.py`**

```python
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "My API"
    ENVIRONMENT: str = "local"          # local | staging | production
    DEBUG: bool = False

    # DynamoDB
    AWS_REGION: str = "us-east-1"
    DYNAMODB_TABLE: str = "myapp-local"
    DYNAMODB_ENDPOINT_URL: str | None = None   # ONLY for local dev (DynamoDB Local / LocalStack)

    # Redis (optional: rate limiting / cache)
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    SECRET_KEY: SecretStr
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
```

**`.env.example`** (commit this; keep the real `.env` out of git)

```env
ENVIRONMENT=local
DEBUG=true
AWS_REGION=us-east-1
DYNAMODB_TABLE=myapp-local
DYNAMODB_ENDPOINT_URL=http://localhost:8001
# Dummy keys, ONLY for DynamoDB Local. Never set real keys here.
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
SECRET_KEY=change-me-to-a-long-random-string-at-least-32-chars
CORS_ORIGINS=["http://localhost:3000"]
```

Generate a strong secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

**AWS credentials in production: do NOT use access keys.** Attach an **IAM role** to your container (ECS task role, EKS IRSA, or Lambda role). `boto3` finds the role credentials automatically. Store `SECRET_KEY` in **AWS Secrets Manager** or SSM Parameter Store.

---

## 6. DynamoDB data modeling (most important part)

### 6.1 Basic words

| Word | Simple meaning |
|---|---|
| **Table** | Container of items (like a SQL table, but flexible) |
| **Item** | One record (like a row), max **400 KB** |
| **Partition key (PK)** | Decides *where* the item is stored. Every query must give an exact PK |
| **Sort key (SK)** | Sorts items inside one PK. Lets you query ranges (`begins_with`, `between`) |
| **GSI** | Global Secondary Index: a second "view" of the table with different keys |
| **Query** | Read items using keys (fast and cheap) ✅ |
| **Scan** | Read the whole table (slow and costly) ❌ avoid in production |
| **TTL** | Automatically deletes items when a timestamp passes |

### 6.2 Step 1: write down your access patterns

For a users + auth system:

| # | Access pattern | How we solve it |
|---|---|---|
| 1 | Get user by id | `GetItem` PK=`USER#<id>`, SK=`PROFILE` |
| 2 | Get user by email (login) | `GetItem` on the email lock item `EMAIL#<email>` → then get user |
| 3 | Make sure email is unique | Transaction with `attribute_not_exists` |
| 4 | List all users (admin), newest first, paginated | Query **GSI1** (`GSI1PK="USERS"`) |
| 5 | Check if a refresh token was revoked | `GetItem` PK=`REVOKED#<jti>` (auto-deleted by TTL) |
| 6 | Rate-limit counters | `UpdateItem` PK=`RATE#...` (auto-deleted by TTL) |

### 6.3 Step 2: single-table design

We store everything in **one table** with generic key names `PK` and `SK`. Different item types are told apart by the **prefix** in the key (`USER#`, `EMAIL#`, ...).

| PK | SK | GSI1PK | GSI1SK | Other attributes |
|---|---|---|---|---|
| `USER#a1b2` | `PROFILE` | `USERS` | `2026-09-28T10:00:00+00:00` | email, full_name, hashed_password, role, is_active |
| `EMAIL#john@x.com` | `LOCK` | | | user_id = `a1b2` |
| `REVOKED#9f3c` | `TOKEN` | | | ttl = 1790000000 |
| `RATE#login#1.2.3.4#29500` | `COUNT` | | | hits = 3, ttl = ... |
| `USER#a1b2` | `ORDER#2026-09-28T11:00:00Z#o1` | | | total, status *(example: orders of a user)* |

**Why the "email lock" item?** DynamoDB has no unique constraint except on the primary key. So we create a second item whose key *is* the email. Creating both items in **one transaction** with `attribute_not_exists(PK)` guarantees that two people can never register the same email. It also gives us a fast "get user by email" lookup with no index.

### 6.4 Example: one-to-many with a sort key

All orders of one user live in the same partition, sorted by time. One query returns them, newest first:

```python
from boto3.dynamodb.conditions import Key

resp = await table.query(
    KeyConditionExpression=Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("ORDER#"),
    ScanIndexForward=False,   # newest first
    Limit=20,
)
orders = resp["Items"]
```

### 6.5 Design rules to remember

- **Never `Scan`** in a request path.
- Pick a partition key with **many different values** (like user id). A key with only a few values (like the constant `"USERS"`) makes a **hot partition** (see note below).
- Store data **together** if you read it together (denormalize). Duplicate data is normal in DynamoDB.
- Use **prefixes** in keys (`USER#`, `ORDER#`) so item types never collide.
- Keep items small; put big files in **S3** and store only the URL.
- Use **Decimal**, never `float`, for numbers (boto3 rejects floats).
- Some attribute names are **reserved words** in DynamoDB (`name`, `status`, `role`, `ttl`, ...). Use `ExpressionAttributeNames` (`#role`) in expressions.

> **Hot partition note:** our GSI1 uses a constant `GSI1PK="USERS"` so an admin can list users. That is fine for an admin-only endpoint with modest traffic. For heavy traffic, **shard** the key (`USERS#0` ... `USERS#9`, pick by `hash(user_id) % 10`) and query all shards.

---

## 7. Creating the table (Terraform + local script)

**Production: define it as code** (`infra/dynamodb.tf`). Never create production tables by clicking around.

```hcl
resource "aws_dynamodb_table" "app" {
  name         = "myapp-prod"
  billing_mode = "PAY_PER_REQUEST"      # on-demand: no capacity planning
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"              # items with an expired "ttl" are deleted automatically
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true                      # restore to any second in the last 35 days
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = {
    Environment = "production"
  }
}
```

**Local development / tests: a reusable Python definition** (`app/db/table_schema.py`)

```python
def table_definition(table_name: str) -> dict:
    return {
        "TableName": table_name,
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    }
```

**`scripts/create_table.py`**

```python
import asyncio

import aioboto3

from app.core.config import settings
from app.db.table_schema import table_definition


async def create_table(client, table_name: str) -> None:
    await client.create_table(**table_definition(table_name))
    await client.get_waiter("table_exists").wait(TableName=table_name)
    await client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )


async def main() -> None:
    session = aioboto3.Session()
    async with session.client(
        "dynamodb",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,
    ) as client:
        try:
            await create_table(client, settings.DYNAMODB_TABLE)
            print("Table created")
        except client.exceptions.ResourceInUseException:
            print("Table already exists")


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `python -m scripts.create_table`

**Changing the schema later (instead of Alembic):**

- New attributes: just start writing them. Old items simply don't have them, so read with `.get("field", default)`.
- Add a `schema_version` attribute to items if formats change a lot, and migrate with a one-off script (paginated `Scan` + `UpdateItem`, run outside request path).
- New access pattern → add a new GSI (via Terraform) and backfill.

---

## 8. Connecting FastAPI to DynamoDB

Create **one** aioboto3 resource when the app starts and reuse it for all requests (creating it per request is slow).

**`app/db/dynamodb.py`**

```python
import aioboto3
from botocore.config import Config
from fastapi import Request

from app.core.config import settings

session = aioboto3.Session()

boto_config = Config(
    region_name=settings.AWS_REGION,
    retries={"max_attempts": 5, "mode": "adaptive"},   # automatic retry with backoff on throttling
    connect_timeout=2,
    read_timeout=5,
    max_pool_connections=50,
)


def create_dynamodb_resource():
    """Use as: async with create_dynamodb_resource() as dynamodb: ..."""
    return session.resource(
        "dynamodb",
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,   # None in production
        config=boto_config,
    )


async def get_table(request: Request):
    """FastAPI dependency: returns the DynamoDB Table object."""
    return await request.app.state.dynamodb.Table(settings.DYNAMODB_TABLE)
```

In `main.py` lifespan (full file in section 24):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with create_dynamodb_resource() as dynamodb:
        app.state.dynamodb = dynamodb      # opened once at startup
        yield
    # connections are closed automatically at shutdown
```

---

## 9. Models, schemas, repositories, services

### Internal model: `app/models/user.py`

```python
from datetime import datetime

from pydantic import BaseModel


class UserInDB(BaseModel):
    """How a user looks inside the app (contains the password hash, never send to clients)."""
    id: str
    email: str
    full_name: str | None = None
    hashed_password: str
    role: str = "user"
    is_active: bool = True
    created_at: datetime
```

### Schemas (API input/output): `app/schemas/user.py`

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
```

> `UserRead` has **no** `hashed_password`. Always use a separate response schema so secrets never leak.

**`app/schemas/token.py`**

```python
from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str
```

### Repository: `app/repositories/user.py`

This is the only place that knows about `PK`, `SK` and `USER#` prefixes.

```python
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.user import UserInDB


def _to_item(user: UserInDB) -> dict:
    created = user.created_at.isoformat()
    item = {
        "PK": f"USER#{user.id}",
        "SK": "PROFILE",
        "GSI1PK": "USERS",              # for "list all users" (GSI1)
        "GSI1SK": created,
        "entity": "User",
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "hashed_password": user.hashed_password,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": created,
    }
    return {k: v for k, v in item.items() if v is not None}   # don't store empty values


def _from_item(item: dict) -> UserInDB:
    return UserInDB.model_validate(item)      # extra keys (PK, SK ...) are ignored


class UserRepository:
    def __init__(self, table):
        self.table = table

    async def get(self, user_id: str) -> UserInDB | None:
        resp = await self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"})
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def get_by_email(self, email: str) -> UserInDB | None:
        # 1) find the email lock item -> gives us the user id
        resp = await self.table.get_item(
            Key={"PK": f"EMAIL#{email.lower()}", "SK": "LOCK"},
            ConsistentRead=True,          # always the latest data (important for login)
        )
        lock = resp.get("Item")
        if not lock:
            return None
        # 2) load the user
        return await self.get(lock["user_id"])

    async def create(self, user: UserInDB) -> UserInDB:
        """Create user + email lock in ONE transaction (all or nothing)."""
        table_name = settings.DYNAMODB_TABLE
        try:
            await self.table.meta.client.transact_write_items(
                TransactItems=[
                    {   # the lock: fails if this email already exists
                        "Put": {
                            "TableName": table_name,
                            "Item": {
                                "PK": f"EMAIL#{user.email.lower()}",
                                "SK": "LOCK",
                                "user_id": user.id,
                            },
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {   # the user itself
                        "Put": {
                            "TableName": table_name,
                            "Item": _to_item(user),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                reasons = e.response.get("CancellationReasons", [])
                if any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
                    raise ConflictError("Email already registered")
            raise
        return user

    async def list(self, limit: int, start_key: dict | None) -> tuple[list[UserInDB], dict | None]:
        """One page of users, oldest first. Returns (users, last_evaluated_key)."""
        kwargs = {
            "IndexName": "GSI1",
            "KeyConditionExpression": Key("GSI1PK").eq("USERS"),
            "Limit": limit,
            "ScanIndexForward": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = await self.table.query(**kwargs)
        return [_from_item(i) for i in resp["Items"]], resp.get("LastEvaluatedKey")
```

> Notes: (1) the resource client accepts normal Python types in `transact_write_items`; (2) transactions cost **2x** the normal write, so use them only where you need all-or-nothing (like this); (3) GSIs are **eventually consistent**, that's fine for an admin list.

### Service: `app/services/user.py`

```python
from app.models.user import UserInDB
from app.repositories.user import UserRepository


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def list_users(self, limit: int, start_key: dict | None) -> tuple[list[UserInDB], dict | None]:
        return await self.users.list(limit=limit, start_key=start_key)
```

---

## 10. DynamoDB cheat sheet

The operations you will use most, with simple examples (`table` is the aioboto3 Table).

**Get one item (fast, cheapest)**

```python
resp = await table.get_item(Key={"PK": "USER#1", "SK": "PROFILE"}, ConsistentRead=True)
item = resp.get("Item")          # None if not found
```

**Create only if it does not exist** (protects from overwriting)

```python
await table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
```

**Update some fields** (never read-modify-write yourself)

```python
await table.update_item(
    Key={"PK": "USER#1", "SK": "PROFILE"},
    UpdateExpression="SET full_name = :n, #role = :r",
    ExpressionAttributeNames={"#role": "role"},              # 'role' is a reserved word
    ExpressionAttributeValues={":n": "John", ":r": "admin"},
    ConditionExpression="attribute_exists(PK)",              # fail if user doesn't exist
    ReturnValues="ALL_NEW",
)
```

**Atomic counter** (safe even with many requests at once)

```python
await table.update_item(
    Key={"PK": "STATS#global", "SK": "COUNTERS"},
    UpdateExpression="ADD signups :one",
    ExpressionAttributeValues={":one": 1},
)
```

**Optimistic locking** (stop two requests overwriting each other): keep a `version` number.

```python
await table.update_item(
    Key={"PK": "USER#1", "SK": "PROFILE"},
    UpdateExpression="SET full_name = :n, version = version + :one",
    ConditionExpression="version = :expected",
    ExpressionAttributeValues={":n": "New Name", ":one": 1, ":expected": 3},
)
# If someone else changed it first, DynamoDB raises ConditionalCheckFailedException -> return 409
```

**Delete**

```python
await table.delete_item(Key={"PK": "USER#1", "SK": "PROFILE"})
```

**Batch read (up to 100 items in one call)**

```python
resp = await table.meta.client.batch_get_item(
    RequestItems={settings.DYNAMODB_TABLE: {"Keys": [{"PK": "USER#1", "SK": "PROFILE"},
                                                    {"PK": "USER#2", "SK": "PROFILE"}]}}
)
# handle resp["UnprocessedKeys"] by retrying (DynamoDB may return only part of the batch)
```

**Handling common errors**

| Error code | Meaning | What to do |
|---|---|---|
| `ConditionalCheckFailedException` | Your condition was false | Return 404/409 |
| `TransactionCanceledException` | A transaction condition failed | Check `CancellationReasons` |
| `ProvisionedThroughputExceededException` / `ThrottlingException` | Too many requests | boto retries; if it persists, raise capacity / fix hot key |
| `ValidationException` | Bad expression or wrong types | Fix code (e.g. you passed a `float`) |
| `ItemCollectionSizeLimitExceededException` | Partition too big (with local indexes) | Redesign keys |

---

## 11. Error handling

Return **the same error format everywhere**.

**`app/core/exceptions.py`**

```python
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppException(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "Something went wrong"):
        self.message = message


class BadRequestError(AppException):
    status_code, code = 400, "bad_request"


class UnauthorizedError(AppException):
    status_code, code = 401, "unauthorized"


class ForbiddenError(AppException):
    status_code, code = 403, "forbidden"


class NotFoundError(AppException):
    status_code, code = 404, "not_found"


class ConflictError(AppException):
    status_code, code = 409, "conflict"


class TooManyRequestsError(AppException):
    status_code, code = 429, "too_many_requests"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "Invalid input", "details": details}},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Internal server error"}},
        )
```

Example response:

```json
{ "error": { "code": "not_found", "message": "User not found" } }
```

---

## 12. JWT authentication (full example)

### 12.1 What is JWT? (simple words)

**JWT = JSON Web Token.** After login the server gives the user a signed "ticket". The user shows it with every request. The server checks the signature and knows who the user is. **No session is stored on the server**, so it scales easily (stateless).

```
xxxxx.yyyyy.zzzzz
header . payload . signature
```

| Part | Contains |
|---|---|
| Header | Algorithm (e.g. HS256) |
| Payload | Claims: `sub` (user id), `exp` (expiry), `type`, `jti` |
| Signature | Proof the token was not changed |

> ⚠️ The payload is only Base64-encoded, **not encrypted**. Never put passwords or private data inside.

### 12.2 The flow

```
1. POST /auth/register   → creates account (password hashed, saved in DynamoDB)
2. POST /auth/login      → email + password
                         ← access_token (15 min) + refresh_token (7 days)
3. GET  /auth/me         → header: Authorization: Bearer <access_token>
4. Access token expires  → API returns 401
5. POST /auth/refresh    → send refresh_token
                         ← NEW access_token + NEW refresh_token (old refresh token is revoked)
6. POST /auth/logout     → refresh token is revoked (stored in DynamoDB until it expires)
```

- **Access token**: short life, sent with every request.
- **Refresh token**: long life, sent **only** to `/refresh` and `/logout`.

### 12.3 Security helpers: `app/core/security.py`

```python
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()      # Argon2 (modern and safe)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_token(subject: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,                # user id
        "type": token_type,            # "access" or "refresh"
        "iat": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,       # unique token id (used for revoking)
    }
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM)


def create_access_token(user_id: str) -> str:
    return create_token(user_id, "access", timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(user_id: str) -> str:
    return create_token(user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str) -> dict:
    """Raises jwt.InvalidTokenError (incl. ExpiredSignatureError) if the token is bad."""
    return jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[settings.ALGORITHM],      # ALWAYS pass the allowed algorithms
    )
```

### 12.4 Token blocklist in DynamoDB (with TTL): `app/repositories/token_blocklist.py`

JWTs cannot be "deleted", so we store the `jti` of revoked refresh tokens. The `ttl` attribute makes DynamoDB **delete the item automatically** after the token would have expired anyway, so the table never fills up with old data.

```python
class TokenBlocklist:
    def __init__(self, table):
        self.table = table

    async def revoke(self, jti: str, expires_at: int) -> None:
        """expires_at = the token's exp claim (unix seconds)."""
        await self.table.put_item(
            Item={"PK": f"REVOKED#{jti}", "SK": "TOKEN", "ttl": expires_at}
        )

    async def is_revoked(self, jti: str) -> bool:
        resp = await self.table.get_item(
            Key={"PK": f"REVOKED#{jti}", "SK": "TOKEN"},
            ConsistentRead=True,
        )
        return "Item" in resp
```

> TTL deletion is **not instant** (can take up to a couple of days). That's fine because an expired JWT is rejected by its own `exp` anyway.

### 12.5 Auth service: `app/services/auth.py`

```python
import uuid
from datetime import datetime, timezone

import jwt

from app.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, hash_password, verify_password,
)
from app.models.user import UserInDB
from app.repositories.token_blocklist import TokenBlocklist
from app.repositories.user import UserRepository
from app.schemas.token import Token
from app.schemas.user import UserCreate


class AuthService:
    def __init__(self, users: UserRepository, blocklist: TokenBlocklist):
        self.users = users
        self.blocklist = blocklist

    async def register(self, data: UserCreate) -> UserInDB:
        user = UserInDB(
            id=uuid.uuid4().hex,
            email=data.email.lower(),
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            created_at=datetime.now(timezone.utc),
        )
        # the repository raises ConflictError if the email already exists (atomic transaction)
        return await self.users.create(user)

    async def login(self, email: str, password: str) -> Token:
        user = await self.users.get_by_email(email.lower())
        # same message for "no user" and "wrong password" so attackers can't find valid emails
        if not user or not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Incorrect email or password")
        if not user.is_active:
            raise ForbiddenError("User is inactive")
        return self._issue_tokens(user.id)

    async def refresh(self, refresh_token: str) -> Token:
        payload = self._decode_refresh(refresh_token)

        if await self.blocklist.is_revoked(payload["jti"]):
            # A used/revoked token came back: could be theft. Reject it.
            raise UnauthorizedError("Refresh token has been revoked")

        user = await self.users.get(payload["sub"])
        if not user or not user.is_active:
            raise UnauthorizedError("User not found or inactive")

        # rotation: the old refresh token can be used only once
        await self.blocklist.revoke(payload["jti"], payload["exp"])
        return self._issue_tokens(user.id)

    async def logout(self, refresh_token: str) -> None:
        payload = self._decode_refresh(refresh_token)
        await self.blocklist.revoke(payload["jti"], payload["exp"])

    @staticmethod
    def _decode_refresh(token: str) -> dict:
        try:
            payload = decode_token(token)
        except jwt.InvalidTokenError:
            raise UnauthorizedError("Invalid or expired refresh token")
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Wrong token type")
        return payload

    @staticmethod
    def _issue_tokens(user_id: str) -> Token:
        return Token(
            access_token=create_access_token(user_id),
            refresh_token=create_refresh_token(user_id),
        )
```

### 12.6 Dependencies: `app/api/deps.py`

```python
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.dynamodb import get_table
from app.models.user import UserInDB
from app.repositories.token_blocklist import TokenBlocklist
from app.repositories.user import UserRepository
from app.services.auth import AuthService
from app.services.user import UserService

# adds the "Authorize" button in Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_auth_service(table=Depends(get_table)) -> AuthService:
    return AuthService(UserRepository(table), TokenBlocklist(table))


def get_user_service(table=Depends(get_table)) -> UserService:
    return UserService(UserRepository(table))


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    table=Depends(get_table),
) -> UserInDB:
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:               # expired, tampered, malformed
        raise UnauthorizedError("Invalid or expired token")

    if payload.get("type") != "access":         # refresh tokens must not work here
        raise UnauthorizedError("Wrong token type")

    user = await UserRepository(table).get(payload["sub"])
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user


def require_role(*roles: str):
    """Use as: Depends(require_role("admin"))"""
    async def checker(user: UserInDB = Depends(get_current_user)) -> UserInDB:
        if user.role not in roles:
            raise ForbiddenError("You do not have permission")
        return user
    return checker
```

> Design choice: we check the blocklist only for **refresh** tokens. Access tokens live only 15 minutes, so a normal request needs **1 DynamoDB read** (the user), not 2. If you need instant logout for access tokens too, add the `is_revoked` check in `get_current_user`.

### 12.7 Endpoints: `app/api/v1/endpoints/auth.py`

```python
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import get_auth_service, get_current_user
from app.core.rate_limit import limiter
from app.models.user import UserInDB
from app.schemas.token import RefreshRequest, Token
from app.schemas.user import UserCreate, UserRead
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def register(request: Request, data: UserCreate, service: AuthService = Depends(get_auth_service)):
    user = await service.register(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")               # protects against password guessing
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),     # fields: username (= email), password
    service: AuthService = Depends(get_auth_service),
):
    return await service.login(email=form.username, password=form.password)


@router.post("/refresh", response_model=Token)
@limiter.limit("20/minute")
async def refresh(request: Request, body: RefreshRequest, service: AuthService = Depends(get_auth_service)):
    return await service.refresh(body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, service: AuthService = Depends(get_auth_service)):
    await service.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
async def me(current_user: UserInDB = Depends(get_current_user)):
    return UserRead.model_validate(current_user)
```

### 12.8 Try it with curl

```bash
# 1) register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"john@example.com","password":"StrongPass123","full_name":"John"}'

# 2) login (form data; "username" is the email)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=john@example.com&password=StrongPass123"

# 3) protected endpoint
curl http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access_token>"

# 4) refresh (old refresh token stops working)
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" -d '{"refresh_token":"<refresh_token>"}'

# 5) logout
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Content-Type: application/json" -d '{"refresh_token":"<refresh_token>"}'
```

### 12.9 Production JWT best practices

| Do | Why |
|---|---|
| Access tokens 5 to 15 minutes | Limits damage if stolen |
| Hash passwords with Argon2/bcrypt | Never store plain passwords |
| Always pass `algorithms=[...]` when decoding | Blocks algorithm-confusion attacks |
| Keep `SECRET_KEY` in Secrets Manager / SSM | Whoever has it can forge tokens |
| HTTPS only | Tokens can be sniffed on plain HTTP |
| Check the `type` claim | Refresh token can't be used as access token |
| Rotate refresh tokens + blocklist | Makes logout real |
| Rate limit login/refresh | Stops brute force |
| Use **RS256** (public/private key) if many services verify tokens | Others need only the public key |
| Browser apps: refresh token in `HttpOnly; Secure; SameSite` cookie | JavaScript (XSS) can't read it |
| Consider **Amazon Cognito** if you don't want to own auth | AWS-managed users, MFA, social login |

---

## 13. Role-based access control (RBAC)

```python
# app/api/v1/endpoints/users.py
from fastapi import APIRouter, Depends

from app.api.deps import get_user_service, require_role
from app.core.pagination import decode_cursor, encode_cursor
from app.models.user import UserInDB
from app.schemas.common import Page, PageParams
from app.schemas.user import UserRead
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=Page[UserRead])
async def list_users(
    params: PageParams = Depends(),
    service: UserService = Depends(get_user_service),
    _admin: UserInDB = Depends(require_role("admin")),      # admins only
):
    start_key = decode_cursor(params.cursor) if params.cursor else None
    users, last_key = await service.list_users(limit=params.limit, start_key=start_key)
    return Page[UserRead](
        items=[UserRead.model_validate(u) for u in users],
        limit=params.limit,
        next_cursor=encode_cursor(last_key) if last_key else None,
    )
```

- **Authentication** = "Who are you?" (JWT)
- **Authorization** = "What can you do?" (roles, permissions, ownership)
- Always check **ownership** too: a normal user must only edit **their own** items. With our keys this is easy: build the key from the *token's* user id (`USER#{current_user.id}`), never from an id sent by the client.

---

## 14. Pagination (cursor based)

DynamoDB has **no offset / page number**. It returns up to `Limit` items and, if there is more, a `LastEvaluatedKey`. You send that key back as `ExclusiveStartKey` to get the next page. We hide it inside an opaque **cursor** string.

### 14.1 Schemas: `app/schemas/common.py`

```python
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class PageParams:
    """Dependency: reads ?limit=20&cursor=... from the URL."""

    def __init__(
        self,
        limit: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
        cursor: str | None = Query(None, description="Cursor from the previous response"),
    ):
        self.limit = limit
        self.cursor = cursor


class Page(BaseModel, Generic[T]):
    items: list[T]
    limit: int
    next_cursor: str | None = None      # None = this was the last page
```

### 14.2 Signed cursor: `app/core/pagination.py`

The cursor is the `LastEvaluatedKey` encoded as base64 and **signed** (HMAC) so clients cannot tamper with it.

```python
import base64
import hashlib
import hmac
import json

from app.core.config import settings
from app.core.exceptions import BadRequestError


def _sign(raw: bytes) -> bytes:
    key = settings.SECRET_KEY.get_secret_value().encode()
    return hmac.new(key, raw, hashlib.sha256).digest()


def encode_cursor(last_evaluated_key: dict) -> str:
    raw = json.dumps(last_evaluated_key, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(_sign(raw) + raw).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        data = base64.urlsafe_b64decode(cursor.encode())
        signature, raw = data[:32], data[32:]
        if not hmac.compare_digest(signature, _sign(raw)):
            raise ValueError("bad signature")
        return json.loads(raw)
    except Exception:
        raise BadRequestError("Invalid cursor")
```

### 14.3 Request and response

```
GET /api/v1/users?limit=2
```

```json
{
  "items": [ {"id": "a1", "email": "..."}, {"id": "b2", "email": "..."} ],
  "limit": 2,
  "next_cursor": "kQ3x...long-string..."
}
```

Next page: `GET /api/v1/users?limit=2&cursor=kQ3x...`

### 14.4 Things to know

- There is **no total count / total pages** (counting all items is slow and costly). If the UI really needs a total, keep a **counter item** updated with `ADD` (section 10).
- DynamoDB may return a `LastEvaluatedKey` even when the next page is empty (when the last page has exactly `limit` items). Clients must handle an empty final page.
- `Limit` is applied **before** `FilterExpression`. If you filter, a page can return fewer than `limit` items (even zero) and still have a next cursor. Prefer designing keys/GSIs so the filter becomes part of the key condition.
- You can only sort by the **sort key** of the table/index. `ScanIndexForward=False` reverses the order (newest first).
- Always cap `limit` (`le=100`).
- A single Query call reads at most **1 MB** of data, so pagination is needed even if you don't set a limit.

---

## 15. Rate limiting

**Rate limiting = limit how many requests one client can send in a time window.** It stops brute-force attacks, scrapers and buggy clients.

Use **layers**:

| Layer | Tool | Best for |
|---|---|---|
| 1. Edge | **AWS WAF rate-based rules**, CloudFront | Stop floods before they reach your app |
| 2. API Gateway | **Usage plans / throttling** (if you use it) | Per API key limits |
| 3. App (shared store) | **slowapi + Redis (ElastiCache)** | Per route / per user limits, high traffic |
| 4. App (no Redis) | **DynamoDB atomic counter** | Sensitive low-traffic routes (login), if you don't want to run Redis |

### 15.1 slowapi with Redis (recommended in-app)

**`app/core/rate_limit.py`**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,           # per client IP
    storage_uri=settings.REDIS_URL,        # shared between all containers
    default_limits=["100/minute"],
)
```

Register in `main.py`:

```python
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
```

Use on a route (the `request: Request` parameter is required):

```python
@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, ...): ...
```

When exceeded, the client receives **HTTP 429 Too Many Requests**.

> **Why a shared store?** With many containers, in-memory counters would be separate in each one and the limit would not work.

### 15.2 No Redis? Use a DynamoDB counter

Each request does one atomic `UpdateItem` that increments a counter for the current time window. `ttl` removes old counters automatically.

```python
# add to app/core/rate_limit.py
import time

from fastapi import Depends, Request

from app.core.exceptions import TooManyRequestsError
from app.db.dynamodb import get_table


def dynamodb_rate_limit(scope: str, limit: int, window_seconds: int):
    """Fixed-window limiter. Use: dependencies=[Depends(dynamodb_rate_limit("login", 5, 60))]"""

    async def dependency(request: Request, table=Depends(get_table)) -> None:
        client_ip = request.client.host if request.client else "unknown"
        window = int(time.time()) // window_seconds
        resp = await table.update_item(
            Key={"PK": f"RATE#{scope}#{client_ip}#{window}", "SK": "COUNT"},
            UpdateExpression="ADD hits :one SET #ttl = :ttl",     # 'ttl' is reserved -> alias
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":one": 1, ":ttl": (window + 2) * window_seconds},
            ReturnValues="UPDATED_NEW",
        )
        if resp["Attributes"]["hits"] > limit:
            raise TooManyRequestsError("Too many requests, please try again later")

    return dependency
```

```python
@router.post("/login", dependencies=[Depends(dynamodb_rate_limit("login", 5, 60))])
async def login(...): ...
```

Trade-offs: every request costs **one write** (money + latency) and one client IP is a hot key. So use this only on a few sensitive routes, and use WAF / Redis for general traffic limits.

### 15.3 Behind a proxy or load balancer

Behind an ALB / CloudFront every request looks like it comes from the proxy IP. Run Uvicorn with proxy headers and trust only your proxy network:

```bash
uvicorn app.main:app --proxy-headers --forwarded-allow-ips="10.0.0.0/8"
```

### 15.4 Limit per user instead of per IP

```python
def user_or_ip_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            return "user:" + decode_token(auth.removeprefix("Bearer "))["sub"]
        except Exception:
            pass
    return get_remote_address(request)
```

---

## 16. REST API design rules

### 16.1 Nouns in URLs, verbs as HTTP methods

| Action | Method | URL | Success |
|---|---|---|---|
| List | GET | `/api/v1/users` | 200 |
| Get one | GET | `/api/v1/users/{id}` | 200 |
| Create | POST | `/api/v1/users` | 201 |
| Replace | PUT | `/api/v1/users/{id}` | 200 |
| Update part | PATCH | `/api/v1/users/{id}` | 200 |
| Delete | DELETE | `/api/v1/users/{id}` | 204 |

❌ `/getUsers`, `/createUser`   ✅ `/users`

### 16.2 Status codes

| Code | Meaning |
|---|---|
| 200 / 201 / 204 | OK / Created / No content |
| 400 | Bad request (e.g. invalid cursor) |
| 401 | Not logged in / bad token |
| 403 | Logged in but not allowed |
| 404 | Not found |
| 409 | Conflict (email exists, version mismatch) |
| 422 | Validation error (FastAPI default) |
| 429 | Rate limit hit |
| 500 / 503 | Server error / dependency down |

### 16.3 Versioning

```python
# app/api/v1/router.py
from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(health.router)
```

Mounted at `/api/v1`. For breaking changes create `/api/v2` and keep v1 running for a while.

### 16.4 Filtering and sorting with DynamoDB

You can only filter/sort efficiently on **keys**. So the "filter" must be part of your key design:

- "Orders of a user, newest first" → `PK=USER#id`, `SK=ORDER#<time>`
- "Orders by status" → add a GSI: `GSI2PK=STATUS#shipped`, `GSI2SK=<time>`
- Free text search ("contains john") → **don't** do it in DynamoDB. Use **OpenSearch** fed by DynamoDB Streams.

### 16.5 Good habits

- Plural nouns, JSON only, `snake_case` fields.
- `response_model` on every endpoint.
- Times in **UTC ISO 8601**; IDs as **UUID** (never sequential numbers) so they spread evenly across partitions and can't be guessed.
- **Idempotency**: `PUT`/`DELETE` give the same result when repeated. For `POST` payments accept an `Idempotency-Key` header and store it with `attribute_not_exists` in DynamoDB.
- OpenAPI docs are automatic at `/docs` (turn off in production if private).

### 16.6 async vs sync in FastAPI

- `async def` + `aioboto3` calls with `await`. **Never** use blocking code (`time.sleep`, normal `boto3`, `requests`) inside `async def`, because it freezes every request.
- If you must use plain `boto3`, use a normal `def` endpoint (FastAPI runs it in a thread pool) or `await run_in_threadpool(...)`.

---

## 17. Logging and request IDs

**`app/core/logging.py`**

```python
import json
import logging
import sys
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)        # stdout -> CloudWatch collects it
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("botocore").setLevel(logging.WARNING)   # botocore is very noisy
```

**`app/middleware/request_id.py`**

```python
import logging
import time
import uuid

from fastapi import Request

from app.core.logging import request_id_ctx

logger = logging.getLogger("access")


async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    token = request_id_ctx.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        logger.info("%s %s took %.1fms", request.method, request.url.path,
                    (time.perf_counter() - start) * 1000)
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response
```

Rules: log to **stdout**; never log passwords, tokens or full items containing secrets; use proper levels; send errors to **Sentry**.

---

## 18. Security best practices (including IAM)

**CORS, trusted hosts, compression, security headers**

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,           # ["https://myapp.com"], never "*" with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.myapp.com"])
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
```

**Least-privilege IAM policy for the app's role.** The app only needs the operations it uses. **No `Scan`, no `DeleteTable`, no `*`.**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:BatchGetItem",
        "dynamodb:ConditionCheckItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:123456789012:table/myapp-prod",
        "arn:aws:dynamodb:us-east-1:123456789012:table/myapp-prod/index/*"
      ]
    }
  ]
}
```

(Advanced: use `dynamodb:LeadingKeys` conditions for per-user row-level access when clients talk to DynamoDB directly.)

**Checklist**

- ✅ HTTPS everywhere (TLS ends at ALB / CloudFront).
- ✅ Validate **all** input with Pydantic (types, lengths, ranges).
- ✅ Build DynamoDB keys and expressions from **validated** values; use `ExpressionAttributeValues` (never format user input into expression strings; that's DynamoDB's version of SQL injection).
- ✅ Hash passwords (Argon2/bcrypt).
- ✅ Encryption at rest (on by default) and **PITR backups** on.
- ✅ Use a **VPC gateway endpoint for DynamoDB** so traffic stays inside AWS (also avoids NAT charges).
- ✅ Hide docs in production: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`.
- ✅ Keep secrets in Secrets Manager / SSM; never in git or images.
- ✅ Scan dependencies and images (`pip-audit`, Dependabot, Trivy, ECR scanning).
- ✅ Never return stack traces to clients.
- ✅ Follow the OWASP API Security Top 10.

---

## 19. Caching

DynamoDB is already fast (single-digit ms), so cache only when needed:

| Option | When |
|---|---|
| **In-app / Redis cache** | Data read very often, changes rarely (config, product list) |
| **DAX** (DynamoDB Accelerator) | Very read-heavy, want microsecond reads without changing much code |
| **Strongly consistent reads** | When you need the latest data (`ConsistentRead=True`), costs 2x a normal read |

Cache-aside example with Redis:

```python
import json

import redis.asyncio as redis

from app.core.config import settings

cache = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_user_cached(user_id: str, repo) -> dict | None:
    key = f"user:{user_id}"
    if cached := await cache.get(key):
        return json.loads(cached)                         # fast path

    user = await repo.get(user_id)                        # DynamoDB
    if user:
        await cache.setex(key, 300, user.model_dump_json(exclude={"hashed_password"}))   # 5 min
        return json.loads(user.model_dump_json(exclude={"hashed_password"}))
    return None
```

Delete the cache key (`await cache.delete(key)`) whenever the item is updated or deleted.

---

## 20. Background jobs and events

- **`BackgroundTasks`**: small, quick jobs that are OK to lose on restart (send welcome email).

```python
from fastapi import BackgroundTasks

@router.post("/register", status_code=201)
async def register(data: UserCreate, background: BackgroundTasks, service=Depends(get_auth_service)):
    user = await service.register(data)
    background.add_task(send_welcome_email, user.email)
    return UserRead.model_validate(user)
```

- **SQS + worker container**: long or important jobs (reports, emails, payments) with retries and a dead-letter queue.
- **DynamoDB Streams + Lambda**: react to data changes automatically (e.g. when an item `entity = "User"` is inserted, send a welcome email, update search index, write an audit log). Very natural with DynamoDB.
- **TTL**: use it for expiring data (sessions, tokens, temporary codes, rate-limit counters) instead of cleanup jobs.

---

## 21. Health checks and monitoring

**`app/api/v1/endpoints/health.py`**

```python
from fastapi import APIRouter, Depends, Response

from app.db.dynamodb import get_table

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
async def liveness():
    """Process is running (no dependencies checked)."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(response: Response, table=Depends(get_table)):
    """Can we reach DynamoDB? A cheap GetItem on a key that doesn't exist."""
    try:
        await table.get_item(Key={"PK": "HEALTH", "SK": "PING"})
        return {"status": "ready"}
    except Exception:
        response.status_code = 503
        return {"status": "dynamodb unavailable"}
```

**Monitoring**

| Pillar | Tool | Answers |
|---|---|---|
| Logs | CloudWatch Logs / Datadog | What happened? |
| Metrics | CloudWatch, Prometheus + Grafana | How is it performing? |
| Traces | OpenTelemetry → AWS X-Ray | Where is it slow? |
| Errors | Sentry | What is crashing? |

**DynamoDB CloudWatch alarms to create:**

| Metric | Why |
|---|---|
| `ThrottledRequests` / `ReadThrottleEvents` / `WriteThrottleEvents` | Hot partition or low capacity |
| `SystemErrors` | AWS-side problems |
| `UserErrors` | Your code sends bad requests |
| `SuccessfulRequestLatency` (p99) | Slow reads/writes |
| `ConsumedReadCapacityUnits` / `ConsumedWriteCapacityUnits` | Cost and capacity trends |

Also set alerts for API error rate (5xx), p95 latency and container CPU/memory.

---

## 22. Testing

For tests use the **moto server** (a fake AWS running locally), or DynamoDB Local via Docker/testcontainers. Moto's server mode works well with async `aioboto3` because it is a real HTTP server.

**`tests/conftest.py`**

```python
import os

# Set env BEFORE importing the app
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long")
os.environ.setdefault("DYNAMODB_TABLE", "test-table")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import aioboto3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.dynamodb import get_table
from app.main import app
from scripts.create_table import create_table

ENDPOINT = "http://127.0.0.1:5555"


@pytest.fixture(scope="session")
def moto_server():
    server = ThreadedMotoServer(port=5555)
    server.start()
    yield
    server.stop()


@pytest_asyncio.fixture
async def client(moto_server):
    limiter.enabled = False                       # no Redis needed in tests
    session = aioboto3.Session()

    async with session.client("dynamodb", endpoint_url=ENDPOINT, region_name="us-east-1") as ddb:
        await create_table(ddb, settings.DYNAMODB_TABLE)

        async with session.resource("dynamodb", endpoint_url=ENDPOINT, region_name="us-east-1") as resource:
            table = await resource.Table(settings.DYNAMODB_TABLE)
            app.dependency_overrides[get_table] = lambda: table   # point the app to the test table

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield c

            app.dependency_overrides.clear()

        await ddb.delete_table(TableName=settings.DYNAMODB_TABLE)   # clean state for the next test
```

**`tests/test_auth.py`**

```python
import pytest

pytestmark = pytest.mark.asyncio

USER = {"email": "a@b.com", "password": "StrongPass123"}


async def _login(client) -> dict:
    r = await client.post("/api/v1/auth/login", data={"username": USER["email"], "password": USER["password"]})
    assert r.status_code == 200
    return r.json()


async def test_register_login_and_me(client):
    r = await client.post("/api/v1/auth/register", json=USER)
    assert r.status_code == 201
    assert "hashed_password" not in r.json()

    tokens = await _login(client)
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200
    assert r.json()["email"] == USER["email"]


async def test_duplicate_email_returns_409(client):
    await client.post("/api/v1/auth/register", json=USER)
    r = await client.post("/api/v1/auth/register", json=USER)
    assert r.status_code == 409


async def test_wrong_password_returns_401(client):
    await client.post("/api/v1/auth/register", json=USER)
    r = await client.post("/api/v1/auth/login", data={"username": USER["email"], "password": "wrong"})
    assert r.status_code == 401


async def test_refresh_token_rotation(client):
    await client.post("/api/v1/auth/register", json=USER)
    tokens = await _login(client)

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200

    # the old refresh token was revoked, so using it again must fail
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
```

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Run: `pytest --cov=app --cov-report=term-missing`

> Moto is a good imitation, but not perfect. Run a small set of **integration tests against DynamoDB Local** (or a real dev AWS account) in CI to catch differences.

---

## 23. Code quality tools

**`pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
```

**`.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

Install: `pre-commit install`

---

## 24. Docker and deployment on AWS

### 24.1 `main.py` (everything together)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.db.dynamodb import create_dynamodb_resource
from app.middleware.request_id import request_id_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("DEBUG" if settings.DEBUG else "INFO")
    async with create_dynamodb_resource() as dynamodb:     # opened once, reused by all requests
        app.state.dynamodb = dynamodb
        yield


def create_app() -> FastAPI:
    is_prod = settings.ENVIRONMENT == "production"
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register_exception_handlers(app)

    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(request_id_middleware)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
```

### 24.2 `Dockerfile`

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

RUN useradd --create-home appuser        # never run as root
USER appuser

ENV PATH="/code/.venv/bin:$PATH"
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live')"

CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn_worker.UvicornWorker", \
     "-w", "4", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "30", \
     "--graceful-timeout", "30"]
```

Workers: about `(2 × CPU cores) + 1`, or 1 worker per container and let ECS/Kubernetes scale the container count.

### 24.3 `docker-compose.yml` (local development)

```yaml
services:
  api:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    environment:
      DYNAMODB_ENDPOINT_URL: http://dynamodb-local:8000     # container-to-container address
      REDIS_URL: redis://redis:6379/0
    depends_on: [dynamodb-local, redis]

  dynamodb-local:
    image: amazon/dynamodb-local:latest
    command: ["-jar", "DynamoDBLocal.jar", "-sharedDb", "-inMemory"]
    ports: ["8001:8000"]          # reachable from your laptop at http://localhost:8001

  redis:
    image: redis:7
```

Start, then create the table once:

```bash
docker compose up -d
python -m scripts.create_table          # uses DYNAMODB_ENDPOINT_URL=http://localhost:8001 from .env
```

### 24.4 Production on AWS (typical setup)

```
Route 53 → CloudFront + WAF → Application Load Balancer (HTTPS, ACM certificate)
         → ECS Fargate service (N stateless FastAPI containers, auto scaling)
              ├── IAM task role  → DynamoDB (via VPC gateway endpoint)
              ├── Secrets Manager → SECRET_KEY
              └── ElastiCache Redis (rate limit / cache, optional)
         → CloudWatch Logs / Alarms, X-Ray, Sentry
```

Other good options: **EKS** (Kubernetes) or **AWS Lambda + Mangum** (serverless; great match for DynamoDB's pay-per-request model, but watch cold starts).

**DynamoDB production settings**

| Setting | Recommendation |
|---|---|
| Capacity mode | **On-demand** to start (spiky/unknown traffic). Switch to **provisioned + auto scaling** when traffic is steady, for lower cost |
| Backups | **PITR on** + scheduled AWS Backup copies |
| Deletion protection | **On** |
| Encryption | On (default), optionally your own KMS key |
| TTL | Enabled on `ttl` attribute |
| Retries | boto `adaptive` retry mode (in our config) |
| Multi-region | **Global Tables** if you need multi-region or very high availability |
| Network | VPC gateway endpoint for DynamoDB |
| Environments | Separate table (or AWS account) per environment: dev / staging / prod |
| Infra changes | Only via Terraform/CDK pull requests |

Keep the app **stateless** (no local files, no in-memory sessions), use **graceful shutdown**, and do **rolling / blue-green** deployments.

---

## 25. CI/CD pipeline

**`.github/workflows/ci.yml`**

```yaml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy app
      - run: uv run pytest --cov=app --cov-fail-under=80

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    permissions:
      id-token: write          # OIDC: no long-lived AWS keys stored in GitHub
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/github-deploy
          aws-region: us-east-1
      - run: docker build -t myapp:${{ github.sha }} .
      # then: push to ECR, terraform apply (infra), update ECS service, smoke test /health/ready
```

Typical pipeline: **lint → type check → test → security scan → build image → push to ECR → terraform plan/apply → deploy → smoke test**.

---

## 26. Final production checklist

**Code & structure**
- [ ] Layered folders (api / services / repositories / models / schemas)
- [ ] Only repositories know DynamoDB keys and expressions
- [ ] Type hints, Ruff + mypy pass
- [ ] Separate request and response schemas
- [ ] API versioning (`/api/v1`)

**DynamoDB**
- [ ] Access patterns written down **before** designing keys
- [ ] No `Scan` in request paths; every read is `GetItem` or `Query`
- [ ] High-cardinality partition keys (no hot keys), UUID ids
- [ ] Uniqueness enforced with conditional writes / transactions
- [ ] `ExpressionAttributeNames` for reserved words; `Decimal` instead of `float`
- [ ] Cursor pagination with a max `limit` and signed cursors
- [ ] TTL on temporary items (tokens, rate-limit counters, sessions)
- [ ] Table, indexes and IAM defined in Terraform/CDK
- [ ] PITR backups and deletion protection enabled
- [ ] CloudWatch alarms on throttling and system errors
- [ ] boto retries and timeouts configured

**Security**
- [ ] HTTPS only, secure headers, strict CORS
- [ ] Passwords hashed with Argon2/bcrypt
- [ ] JWT: short access token, refresh rotation, blocklist, secret in Secrets Manager
- [ ] IAM role with least privilege (no access keys in code/env in production)
- [ ] Rate limiting at WAF + app level, stricter on login/register
- [ ] Input validation everywhere; expressions use placeholders
- [ ] Docs disabled or protected in production
- [ ] Dependency and image vulnerability scans

**Reliability & operations**
- [ ] Consistent error format + global exception handler
- [ ] Health checks (`/live`, `/ready`)
- [ ] JSON logs with request IDs; metrics; tracing; Sentry
- [ ] Timeouts on all outgoing calls
- [ ] Background jobs (SQS / Streams) for slow work
- [ ] Graceful shutdown, stateless containers

**Delivery**
- [ ] Dockerfile (non-root, small image)
- [ ] `.env.example` committed; real secrets never in git
- [ ] Automated tests with coverage in CI
- [ ] Automated deploy with rollback plan

---

### Common DynamoDB mistakes to avoid

1. Designing tables like SQL and then needing joins or `Scan`.
2. Using a low-cardinality partition key (`status`, `type`, a constant) that creates a hot partition.
3. Using `float` numbers (use `Decimal`), or forgetting reserved words like `role`, `name`, `status`, `ttl`.
4. Expecting page numbers or a total count.
5. Reading, changing in Python, then writing back (race conditions). Use `UpdateItem` with expressions and conditions instead.
6. Forgetting that GSIs are eventually consistent.
7. Storing big blobs in items (400 KB limit). Use S3.
8. Creating the boto resource on every request instead of once at startup.
9. Committing AWS access keys instead of using IAM roles.
10. Changing production tables manually instead of through code.

### Suggested learning order

1. Write your access patterns; design the keys (section 6).
2. Run DynamoDB Local with Docker; create the table with the script.
3. Build the repository + register/login with JWT.
4. Add protected routes, roles, and cursor pagination.
5. Add rate limiting, logging, and error handling.
6. Write tests with moto, then Dockerize.
7. Define infra with Terraform, add CI/CD, monitoring, and deploy to AWS.

Good luck with your FastAPI + DynamoDB project! 🚀
