"""Provision the versioned local Metabase dashboard via its pinned-version API."""

import json
import os
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = json.loads((ROOT / "dashboard/pt_br.json").read_text())
LEGACY_MANAGED = "Managed by GitLog: dashboard/cards.json."
MANAGED = TRANSLATIONS["managed"]
NAME = TRANSLATIONS["name"]
LEGACY_NAME = TRANSLATIONS["legacy_name"]
RECRUITER_EMAIL = "user@teste.com"
RECRUITER_GROUP = "GitLog — Recrutadores"
RECRUITER_COLLECTION = "read"
PARAMETERS = [
    {
        "id": "repository",
        "name": TRANSLATIONS["filters"]["repository"],
        "slug": "repository",
        "type": "string/=",
        "isMultiSelect": True,
    },
    {
        "id": "date_range",
        "name": TRANSLATIONS["filters"]["date_range"],
        "slug": "date",
        "type": "date/all-options",
    },
    {
        "id": "language",
        "name": TRANSLATIONS["filters"]["language"],
        "slug": "language",
        "type": "string/=",
        "isMultiSelect": True,
    },
]


def api(client, method, path, payload=None):
    response = client.request(method, path, json=payload)
    if response.is_error:
        # Error bodies can contain connection parameters; do not print them.
        raise RuntimeError(f"Metabase {method} {path}: HTTP {response.status_code}")
    return response.json() if response.content else None


def items(result):
    return result.get("data", []) if isinstance(result, dict) else result


def find_named(rows, name, *aliases):
    found = [row for row in rows if row["name"] in (name, *aliases)]
    if len(found) > 1:
        raise ValueError(f"Ambiguous Metabase object: {name}")
    return found[0] if found else None


def require_managed(row):
    if row and not (row.get("description") or "").startswith((MANAGED, LEGACY_MANAGED)):
        raise ValueError("Existing collection/dashboard is not managed by GitLog.")


def setup(client, email, password):
    properties = api(client, "GET", "/api/session/properties")
    if properties.get("setup-token"):
        api(
            client,
            "POST",
            "/api/setup",
            {
                "token": properties["setup-token"],
                "user": {
                    "email": email,
                    "password": password,
                    "first_name": "GitLog",
                    "last_name": "Admin",
                },
                "prefs": {"site_name": NAME, "site_locale": TRANSLATIONS["locale"]},
            },
        )
    session = api(
        client, "POST", "/api/session", {"username": email, "password": password}
    )
    client.headers["X-Metabase-Session"] = session["id"]


def configure_localization(client):
    properties = api(client, "GET", "/api/session/properties")
    if properties.get("site-locale") != TRANSLATIONS["locale"]:
        api(
            client, "PUT", "/api/setting/site-locale", {"value": TRANSLATIONS["locale"]}
        )
    api(client, "PUT", "/api/setting/site-name", {"value": NAME})
    user = api(client, "GET", "/api/user/current")
    if user.get("locale") != TRANSLATIONS["locale"]:
        api(
            client, "PUT", f"/api/user/{user['id']}", {"locale": TRANSLATIONS["locale"]}
        )


def localize_metadata(client, metadata):
    for table in metadata["tables"]:
        if (
            table["schema"] != "analytics"
            or table["name"] not in TRANSLATIONS["tables"]
        ):
            continue
        translated = TRANSLATIONS["tables"][table["name"]]
        api(
            client,
            "PUT",
            f"/api/table/{table['id']}",
            {
                "display_name": translated["name"],
                "description": translated["description"],
            },
        )
        for field in table["fields"]:
            label = TRANSLATIONS["fields"].get(field["name"])
            if label:
                api(
                    client,
                    "PUT",
                    f"/api/field/{field['id']}",
                    {
                        "display_name": label,
                        "description": TRANSLATIONS["field_descriptions"].get(
                            field["name"], label + "."
                        ),
                    },
                )


def provision_dashboard(client, database_details):
    database = find_named(items(api(client, "GET", "/api/database")), NAME, LEGACY_NAME)
    if database:
        for field in ("host", "dbname", "user"):
            if database["details"].get(field) != database_details[field]:
                raise ValueError(
                    "Existing data source has different connection settings."
                )
    else:
        database = api(
            client,
            "POST",
            "/api/database",
            {
                "name": NAME,
                "engine": "postgres",
                "details": database_details,
                "is_full_sync": True,
                "auto_run_queries": True,
            },
        )
    database_id = database["id"]
    if database["name"] != NAME:
        api(client, "PUT", f"/api/database/{database_id}", {"name": NAME})
    api(client, "POST", f"/api/database/{database_id}/sync_schema")
    definitions = json.loads((ROOT / "dashboard/cards.json").read_text())
    needed = {
        tuple(field) for card in definitions for field in card["filters"].values()
    }
    fields = {}
    for _ in range(60):
        metadata = api(client, "GET", f"/api/database/{database_id}/metadata")
        fields = {
            (table["name"], field["name"]): field["id"]
            for table in metadata["tables"]
            if table["schema"] == "analytics"
            for field in table["fields"]
        }
        if needed <= fields.keys():
            break
        time.sleep(2)
    else:
        raise RuntimeError(
            "Analytics fields unavailable after sync. Run dbt run/test first."
        )
    collection = find_named(
        items(api(client, "GET", "/api/collection")), NAME, LEGACY_NAME
    )
    require_managed(collection)
    if collection is None:
        collection = api(
            client, "POST", "/api/collection", {"name": NAME, "description": MANAGED}
        )
    collection_id = collection["id"]
    api(
        client,
        "PUT",
        f"/api/collection/{collection_id}",
        {"name": NAME, "description": MANAGED},
    )
    localize_metadata(client, metadata)
    contents = items(api(client, "GET", f"/api/collection/{collection_id}/items"))
    dashboard = find_named(
        [row for row in contents if row["model"] == "dashboard"], NAME, LEGACY_NAME
    )
    if dashboard:
        dashboard = api(client, "GET", f"/api/dashboard/{dashboard['id']}")
        require_managed(dashboard)
    dashboard_payload = {
        "name": NAME,
        "collection_id": collection_id,
        "description": MANAGED + " Eventos em UTC e metadados atuais dos repositórios. "
        "Sem histórico artificial.",
        "parameters": PARAMETERS,
    }
    if dashboard:
        dashboard = api(
            client, "PUT", f"/api/dashboard/{dashboard['id']}", dashboard_payload
        )
    else:
        dashboard = api(client, "POST", "/api/dashboard", dashboard_payload)
    previous = {row["card_id"]: row["id"] for row in dashboard.get("dashcards", [])}
    placements = []
    for index, definition in enumerate(definitions):
        tags = {}
        for name, field in definition["filters"].items():
            tags[name] = {
                "id": str(uuid5(NAMESPACE_URL, definition["key"] + "/" + name)),
                "name": name,
                "display-name": TRANSLATIONS["filters"][name],
                "type": "dimension",
                "dimension": ["field", fields[tuple(field)], None],
                "widget-type": (
                    "date/all-options" if name == "date_range" else "category"
                ),
                "required": False,
            }
        query = {
            "database": database_id,
            "type": "native",
            "native": {
                "query": (
                    ROOT / "dashboard/queries" / f"{definition['key']}.sql"
                ).read_text(),
                "template-tags": tags,
            },
        }
        card_payload = {
            "name": definition["name"],
            "collection_id": collection_id,
            "description": MANAGED + " " + definition["description"],
            "display": definition["display"],
            "dataset_query": query,
            "visualization_settings": definition["visualization_settings"],
        }
        existing = find_named(
            [row for row in contents if row["model"] == "card"],
            definition["name"],
            definition["legacy_name"],
        )
        if existing:
            require_managed(api(client, "GET", f"/api/card/{existing['id']}"))
            card = api(client, "PUT", f"/api/card/{existing['id']}", card_payload)
        else:
            card = api(client, "POST", "/api/card", card_payload)
        result = api(
            client,
            "POST",
            f"/api/card/{card['id']}/query",
            {"parameters": [], "ignore_cache": True},
        )
        if result.get("status") != "completed":
            raise RuntimeError(f"Card query failed: {definition['key']}")
        scalar = index < 6
        placements.append(
            {
                "id": previous.get(card["id"], -(index + 1)),
                "card_id": card["id"],
                "row": 0 if scalar else 4 + ((index - 6) // 2) * 7,
                "col": index * 4 if scalar else ((index - 6) % 2) * 12,
                "size_x": 4 if scalar else 12,
                "size_y": 4 if scalar else 7,
                "parameter_mappings": [
                    {
                        "parameter_id": name,
                        "card_id": card["id"],
                        "target": ["dimension", ["template-tag", name]],
                    }
                    for name in tags
                ],
            }
        )
    api(client, "PUT", f"/api/dashboard/{dashboard['id']}/cards", {"cards": placements})
    return dashboard["id"], len(placements)


def provision_recruiter(client, database_id, collection_id, dashboard_id):
    """Create a persistent dashboard-only account and least-privilege permissions."""
    groups = items(api(client, "GET", "/api/permissions/group"))
    all_users = next(
        (
            group
            for group in groups
            if group["magic_group_type"] == "all-internal-users"
        ),
        None,
    )
    if not all_users:
        raise ValueError("Metabase All Users group is missing.")
    group = find_named(groups, RECRUITER_GROUP)
    if group and group["magic_group_type"]:
        raise ValueError("Recruiter group name is reserved by another Metabase group.")
    if group is None:
        group = api(client, "POST", "/api/permissions/group", {"name": RECRUITER_GROUP})

    users = items(api(client, "GET", "/api/user"))
    configured_email = os.environ.get("METABASE_RECRUITER_EMAIL", RECRUITER_EMAIL)
    user = next(
        (
            row
            for row in users
            if row["email"].casefold() == configured_email.casefold()
        ),
        None,
    )
    if user and user.get("is_superuser"):
        raise ValueError("The recruiter login already belongs to an administrator.")
    properties = api(client, "GET", "/api/session/properties")
    if user is None:
        if properties.get("email-configured?"):
            raise ValueError(
                "Email is configured; refusing to trigger a Metabase invitation email."
            )
        api(
            client,
            "POST",
            "/api/user",
            {
                "email": configured_email,
                "first_name": "GitLog",
                "last_name": "Recrutador",
                "password": os.environ["METABASE_RECRUITER_PASSWORD"],
            },
        )
        users = items(api(client, "GET", "/api/user"))
        user = next(
            row
            for row in users
            if row["email"].casefold() == configured_email.casefold()
        )
        api(
            client,
            "PUT",
            f"/api/user/{user['id']}",
            {"locale": TRANSLATIONS["locale"]},
        )

    # A login used by recruiters must belong only to All Users and this viewer group.
    memberships = api(client, "GET", "/api/permissions/membership")
    current = memberships.get(str(user["id"]), [])
    member_group_ids = {item["group_id"] for item in current}
    if group["id"] not in member_group_ids:
        api(
            client,
            "POST",
            "/api/permissions/membership",
            {"group_id": group["id"], "user_id": user["id"]},
        )
    for membership in current:
        if membership["group_id"] not in {all_users["id"], group["id"]}:
            api(
                client,
                "DELETE",
                f"/api/permissions/membership/{membership['membership_id']}",
            )

    # Metabase permissions are additive: narrow the built-in All Users baseline
    # so its defaults cannot grant this account SQL, raw data, or other collections.
    graph = api(client, "GET", "/api/permissions/graph")
    everyone = graph["groups"][str(all_users["id"])]
    everyone[str(database_id)] = {
        "view-data": "unrestricted",
        "create-queries": "no",
        "download": {"schemas": "none"},
        "data-model": {"schemas": "none"},
        "details": "no",
        "transforms": "no",
    }
    # Newly-created Metabase groups receive permissive defaults. Override those
    # defaults for every current data source, including Metabase's sample DB.
    recruiter_database_permissions = graph["groups"].setdefault(str(group["id"]), {})
    for source_id in recruiter_database_permissions:
        recruiter_database_permissions[source_id] = {
            "view-data": "unrestricted",
            "create-queries": "no",
            "download": {"schemas": "none"},
            "data-model": {"schemas": "none"},
            "details": "no",
            "transforms": "no",
        }
    api(client, "PUT", "/api/permissions/graph", graph)

    collections = api(client, "GET", "/api/collection/graph")
    everyone_collections = collections["groups"][str(all_users["id"])]
    everyone_collections["root"] = "none"
    everyone_collections[str(collection_id)] = "none"
    recruiter_collections = collections["groups"].setdefault(str(group["id"]), {})
    recruiter_collections["root"] = "none"
    recruiter_collections[str(collection_id)] = RECRUITER_COLLECTION
    api(
        client,
        "PUT",
        "/api/collection/graph",
        {"revision": collections["revision"], "groups": collections["groups"]},
    )

    # Check membership, non-admin role, and the effective collection/database policy.
    confirmed = next(
        row for row in items(api(client, "GET", "/api/user")) if row["id"] == user["id"]
    )
    if confirmed.get("is_superuser"):
        raise ValueError("The recruiter login unexpectedly has administrator access.")
    collection_access = api(client, "GET", "/api/collection/graph")
    if collection_access["groups"][str(group["id"])].get(str(collection_id)) != "read":
        raise ValueError("Could not confirm read access to the analytics collection.")
    permissions = api(client, "GET", "/api/permissions/graph")
    default_database_permissions = permissions["groups"][str(all_users["id"])].get(
        str(database_id), {}
    )
    if default_database_permissions.get("create-queries", "no") != "no":
        raise ValueError("Could not confirm that ad-hoc query creation is disabled.")
    return user["id"]


def main():
    load_dotenv(ROOT / ".env", override=False)
    base_url = os.environ.get(
        "METABASE_URL", f"http://127.0.0.1:{os.environ.get('METABASE_PORT', '3000')}"
    )
    email = os.environ.get("METABASE_ADMIN_EMAIL", "")
    password = os.environ.get("METABASE_ADMIN_PASSWORD", "")
    try:
        recruiter_password = os.environ.get("METABASE_RECRUITER_PASSWORD", "")
        if not email or not password or not os.environ.get("METABASE_READER_PASSWORD"):
            raise ValueError(
                "Configure Metabase credentials in .env; see docs/dashboard.md."
            )
        if not recruiter_password or len(recruiter_password) < 15:
            raise ValueError(
                "METABASE_RECRUITER_PASSWORD must have at least 15 characters."
            )
        details = {
            "host": "postgres",
            "port": 5432,
            "dbname": os.environ.get("POSTGRES_DB", "gitlog"),
            "user": os.environ.get("METABASE_READER_USER", "gitlog_metabase"),
            "password": os.environ["METABASE_READER_PASSWORD"],
            "ssl": False,
            "schema-filters-type": "inclusion",
            "schema-filters-patterns": "analytics",
        }
        with httpx.Client(base_url=base_url, timeout=120, trust_env=False) as client:
            setup(client, email, password)
            configure_localization(client)
            dashboard_id, count = provision_dashboard(client, details)
            database = find_named(items(api(client, "GET", "/api/database")), NAME)
            collection = find_named(items(api(client, "GET", "/api/collection")), NAME)
            recruiter_id = provision_recruiter(
                client, database["id"], collection["id"], dashboard_id
            )
        print(
            f"{NAME}: {base_url}/dashboard/{dashboard_id} ({count} cartões validados); "
            "conta de demonstração "
            f"{os.environ.get('METABASE_RECRUITER_EMAIL', RECRUITER_EMAIL)} "
            f"provisionada (ID {recruiter_id})"
        )
        return 0
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        print(
            f"Metabase setup failed ({type(error).__name__}). "
            "See configuration and API version."
        )
        # Our RuntimeError messages contain only safe paths and status codes.
        if isinstance(error, RuntimeError):
            print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
