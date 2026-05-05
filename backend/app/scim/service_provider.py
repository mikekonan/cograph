"""SCIM 2.0 metadata responses (ServiceProviderConfig, ResourceTypes, Schemas).

Cograph implements a deliberately small subset of RFC 7644:

- Filter:     supported, but only `userName eq` and `externalId eq`.
- Patch:      supported (op: replace, add, remove).
- Sort:       not supported.
- ETag:       not supported.
- Bulk:       not supported.
- Change pwd: not supported.

Everything below is static. We do not negotiate features per request.
"""

from __future__ import annotations

from typing import Any

from backend.app.scim.resource import SCIM_USER_SCHEMA


def service_provider_config() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://github.com/mikekonan/cograph#readme",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "name": "Bearer Token",
                "description": "SCIM bearer token minted by Cograph admin",
                "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                "type": "oauthbearertoken",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": "/scim/v2/ServiceProviderConfig",
        },
    }


def resource_types() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "startIndex": 1,
        "itemsPerPage": 1,
        "Resources": [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "schema": SCIM_USER_SCHEMA,
                "meta": {
                    "resourceType": "ResourceType",
                    "location": "/scim/v2/ResourceTypes/User",
                },
            }
        ],
    }


def schemas() -> dict[str, Any]:
    """Minimal User core schema. Includes only the fields we read."""

    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "startIndex": 1,
        "itemsPerPage": 1,
        "Resources": [
            {
                "id": SCIM_USER_SCHEMA,
                "name": "User",
                "description": "SCIM core User (subset).",
                "attributes": [
                    {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
                    {"name": "active", "type": "boolean", "required": False},
                    {
                        "name": "name",
                        "type": "complex",
                        "subAttributes": [
                            {"name": "givenName", "type": "string"},
                            {"name": "familyName", "type": "string"},
                            {"name": "formatted", "type": "string"},
                        ],
                    },
                    {
                        "name": "emails",
                        "type": "complex",
                        "multiValued": True,
                        "subAttributes": [
                            {"name": "value", "type": "string"},
                            {"name": "primary", "type": "boolean"},
                            {"name": "type", "type": "string"},
                        ],
                    },
                    {"name": "externalId", "type": "string", "required": False},
                ],
                "meta": {"resourceType": "Schema", "location": f"/scim/v2/Schemas/{SCIM_USER_SCHEMA}"},
            }
        ],
    }
