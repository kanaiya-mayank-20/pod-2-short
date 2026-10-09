"""Table definition for local development and tests.

Production tables are created from Terraform, never by this script. Item types are
told apart by the prefix in the key:

======================================  ==========================  ===========================
Item                                    PK                          SK
======================================  ==========================  ===========================
User profile                            ``USER#{userId}``          ``PROFILE``
Email lock (unique email)               ``EMAIL#{email}``           ``LOCK``
Refresh token (per device)              ``USER#{userId}``           ``TOKEN#{deviceId}``
Job                                     ``USER#{userId}``           ``JOB#{jobId}``
Clip                                    ``USER#{userId}``           ``JOB#{jobId}#CLIP#{clipId}``
======================================  ==========================  ===========================

``ttl`` items (refresh tokens) are removed by DynamoDB once expired.
Clip-version items use ``JOB#{jobId}#CLIP#{titleId}#VERSION#{versionId}`` and are
kept separate from title items so each render can be tracked and deleted independently.
"""

from typing import Any

PK = "PK"
SK = "SK"
TTL_ATTRIBUTE = "ttl"


def table_definition(table_name: str) -> dict[str, Any]:
    return {
        "TableName": table_name,
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [
            {"AttributeName": PK, "KeyType": "HASH"},
            {"AttributeName": SK, "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": PK, "AttributeType": "S"},
            {"AttributeName": SK, "AttributeType": "S"},
        ],
    }
