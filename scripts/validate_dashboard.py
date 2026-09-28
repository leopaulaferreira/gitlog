"""Exercise real BI queries and filters; save timings without source payloads."""

import json
import os
import re
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv

from ingestion.config import DatabaseSettings
from scripts.setup_metabase import NAME, ROOT, api, find_named, items, setup


def validate():
    load_dotenv(ROOT / ".env", override=False)
    settings = DatabaseSettings.from_env()
    kwargs = settings.connection_kwargs()
    kwargs.update(
        user=os.environ.get("METABASE_READER_USER", "gitlog_metabase"),
        password=os.environ["METABASE_READER_PASSWORD"],
    )
    report = {"query_plans": [], "metabase": []}
    with psycopg.connect(**kwargs, autocommit=True) as connection:
        repository = connection.execute(
            "SELECT full_name, primary_language FROM analytics.dim_repository "
            "ORDER BY full_name LIMIT 1"
        ).fetchone()
        for path in sorted((ROOT / "dashboard/queries").glob("*.sql")):
            query = re.sub(r"\[\[.*?\]\]", "", path.read_text(), flags=re.S)
            plan = connection.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
            ).fetchone()[0][0]
            report["query_plans"].append(
                {
                    "card": path.stem,
                    "execution_ms": plan["Execution Time"],
                    "rows": plan["Plan"]["Actual Rows"],
                }
            )
        try:
            connection.execute("SELECT count(*) FROM raw.repositories")
        except psycopg.errors.InsufficientPrivilege:
            report["reader_raw_access"] = "denied"
        else:
            raise RuntimeError("BI role unexpectedly has raw access.")
    with httpx.Client(
        base_url=f"http://127.0.0.1:{os.environ.get('METABASE_PORT','3000')}",
        timeout=120,
        trust_env=False,
    ) as client:
        setup(
            client,
            os.environ["METABASE_ADMIN_EMAIL"],
            os.environ["METABASE_ADMIN_PASSWORD"],
        )
        collection = find_named(items(api(client, "GET", "/api/collection")), NAME)
        contents = items(
            api(client, "GET", f"/api/collection/{collection['id']}/items")
        )
        cards = [row for row in contents if row["model"] == "card"]
        dashboards = [row for row in contents if row["model"] == "dashboard"]
        definitions = json.loads((ROOT / "dashboard/cards.json").read_text())
        if len(cards) != len(definitions) or len(dashboards) != 1:
            raise RuntimeError("Unexpected managed dashboard/card count.")
        dashboard = api(client, "GET", f"/api/dashboard/{dashboards[0]['id']}")
        if len(dashboard["dashcards"]) != len(cards):
            raise RuntimeError("Dashboard is missing cards.")
        for card_summary in cards:
            card_id = card_summary["id"]
            card = api(client, "GET", f"/api/card/{card_id}")
            query = card["dataset_query"]
            # Metabase 0.63 normalizes stored queries to pMBQL.
            tags = query["stages"][0]["template-tags"]
            params = []
            for tag in tags:
                name = tag["name"]
                if name == "language" and (not repository or not repository[1]):
                    continue
                if name == "date_range":
                    kind, value = "date/range", "2000-01-01~2100-01-01"
                else:
                    kind = "string/="
                    value = (
                        [repository[0] if name == "repository" else repository[1]]
                        if repository
                        else ["no-such/repository"]
                    )
                params.append(
                    {
                        "id": tag["id"],
                        "type": kind,
                        "target": ["dimension", ["template-tag", name]],
                        "value": value,
                    }
                )
            result = api(
                client,
                "POST",
                f"/api/card/{card_id}/query",
                {"parameters": params, "ignore_cache": True},
            )
            if result.get("status") != "completed":
                raise RuntimeError(f"Filtered query failed: {card_summary['name']}")
            # A nonmatching repository and an empty time period must remove data.
            for name, kind, value in [
                ("repository", "string/=", ["no-such/repository"]),
                ("date_range", "date/range", "0001-01-01~0001-01-02"),
            ]:
                tag = next((t for t in tags if t["name"] == name), None)
                if tag is None:
                    continue
                result = api(
                    client,
                    "POST",
                    f"/api/card/{card_id}/query",
                    {
                        "parameters": [
                            {
                                "id": tag["id"],
                                "type": kind,
                                "target": ["dimension", ["template-tag", name]],
                                "value": value,
                            }
                        ],
                        "ignore_cache": True,
                    },
                )
                if result.get("status") != "completed" or result["data"][
                    "rows"
                ] not in ([], [[0]]):
                    raise RuntimeError(
                        f"Filter did not exclude rows: {card_summary['name']}"
                    )
            report["metabase"].append(
                {"card": card_summary["name"], "filters": "passed"}
            )
    output = Path("data/validation/phase7-dashboard-validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(
        f"Validated {len(cards)} cards and filters. BI access restricted to analytics."
    )
    slowest = max(row["execution_ms"] for row in report["query_plans"])
    print(f"Slowest local query: {slowest:.3f} ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate())
