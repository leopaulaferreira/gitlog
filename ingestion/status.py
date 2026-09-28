"""Read-only, consistent snapshot of durable audit and checkpoint state."""

import json

import psycopg
from psycopg.rows import dict_row


def snapshot(connection: psycopg.Connection) -> dict:
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT DISTINCT ON (repository, pipeline_name) "
                "id, repository, pipeline_name, status, started_at, finished_at, "
                "records_extracted, records_loaded, records_skipped, duration_ms, "
                "error_message FROM raw.pipeline_runs "
                "ORDER BY repository, pipeline_name, started_at DESC, id DESC"
            )
            pipelines = cursor.fetchall()
            cursor.execute(
                "SELECT id, repository, pipeline_name, started_at "
                "FROM raw.pipeline_runs WHERE status='RUNNING' ORDER BY started_at"
            )
            pending = cursor.fetchall()
            cursor.execute(
                "SELECT cp.repository_id, repo.full_name AS repository, "
                "'commits' AS entity, cp.head_sha AS value, cp.updated_at, "
                "cp.pipeline_run_id, coalesce(r.status='SUCCESS' "
                "AND r.pipeline_name='commit_ingestion' AND c.sha IS NOT NULL, false) "
                "AS consistent FROM raw.ingestion_checkpoints cp "
                "JOIN raw.repositories repo ON repo.id=cp.repository_id "
                "LEFT JOIN raw.pipeline_runs r ON r.id=cp.pipeline_run_id "
                "LEFT JOIN raw.commits c ON c.repository_id=cp.repository_id "
                "AND c.sha=cp.head_sha UNION ALL "
                "SELECT cp.repository_id, repo.full_name, cp.entity, "
                "cp.watermark::text, cp.updated_at, cp.pipeline_run_id, "
                "coalesce(r.status='SUCCESS' "
                "AND r.pipeline_name=cp.entity||'_ingestion' "
                "AND cp.watermark <= now()+interval '5 minutes', false) "
                "FROM raw.entity_checkpoints cp "
                "JOIN raw.repositories repo ON repo.id=cp.repository_id "
                "LEFT JOIN raw.pipeline_runs r ON r.id=cp.pipeline_run_id "
                "ORDER BY repository, entity"
            )
            checkpoints = cursor.fetchall()
            cursor.execute(
                "SELECT count(*) AS issue_pr_overlap FROM raw.issues i "
                "JOIN raw.pull_requests p USING (repository_id, number)"
            )
            quality = cursor.fetchone()
    latest = max(
        pipelines, key=lambda row: (row["started_at"], row["id"]), default=None
    )
    return {
        "last_run": latest,
        "pipelines": pipelines,
        "checkpoints": checkpoints,
        "pending_runs": pending,
        "quality": quality,
    }


def print_status(report: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, default=str, ensure_ascii=False))
        return
    latest = report["last_run"]
    print(
        f"Last run: {latest['id']} ({latest['status']})" if latest else "No runs yet."
    )
    for row in report["pipelines"]:
        print(
            f"Repository: {row['repository']} | Pipeline: {row['pipeline_name']} | "
            f"Status: {row['status']} | Extracted: {row['records_extracted']} | "
            f"Loaded: {row['records_loaded']} | Skipped: {row['records_skipped']} | "
            f"Duration (ms): {row['duration_ms']} | Started: {row['started_at']}"
        )
    print("Checkpoints:")
    for row in report["checkpoints"]:
        print(
            f"  {row['repository']} | {row['entity']} | {row['value']} | "
            f"{'OK' if row['consistent'] else 'INCONSISTENT'}"
        )
    print(f"Pending RUNNING audits: {len(report['pending_runs'])}")
    for row in report["pending_runs"]:
        print(
            f"  {row['id']} | {row['repository']} | {row['pipeline_name']} | "
            f"Started: {row['started_at']}"
        )
    print(f"Issue/PR overlaps: {report['quality']['issue_pr_overlap']}")
