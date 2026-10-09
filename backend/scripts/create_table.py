"""Creates the single table in DynamoDB Local (development and tests only).

Production tables are managed by Terraform; this script exists so a developer can get
a working table in seconds. Start DynamoDB Local first (no Dockerfile lives in this
repository, the container is only for local development):

    docker run --rm -d --name dynamodb-local -p 8001:8000 amazon/dynamodb-local \
        -jar DynamoDBLocal.jar -sharedDb -inMemory

then:

    python -m scripts.create_table
"""

import asyncio
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from core.config import settings
from db.table_schema import TTL_ATTRIBUTE, table_definition


async def create_table(client: Any, table_name: str) -> None:
    try:
        await client.create_table(**table_definition(table_name))
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceInUseException":
            print(f"Table {table_name} already exists")
            return
        raise
    await client.get_waiter("table_exists").wait(TableName=table_name)
    await client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
    )
    print(f"Table {table_name} created")


async def main() -> None:
    session = aioboto3.Session()
    async with session.client(
        "dynamodb",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,
    ) as client:
        await create_table(client, settings.DYNAMODB_TABLE)


if __name__ == "__main__":
    asyncio.run(main())
