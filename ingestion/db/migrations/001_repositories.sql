CREATE SCHEMA raw;

CREATE TABLE raw.pipeline_runs (
    id uuid PRIMARY KEY,
    pipeline_name text NOT NULL,
    repository text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    records_extracted integer NOT NULL DEFAULT 0 CHECK (records_extracted >= 0),
    records_loaded integer NOT NULL DEFAULT 0 CHECK (records_loaded >= 0),
    raw_path text,
    error_message text,
    CHECK (records_loaded <= records_extracted),
    CHECK ((status = 'RUNNING' AND finished_at IS NULL)
        OR (status IN ('SUCCESS', 'FAILED') AND finished_at IS NOT NULL)),
    CHECK ((status = 'FAILED' AND error_message IS NOT NULL)
        OR (status <> 'FAILED' AND error_message IS NULL))
);
CREATE INDEX pipeline_runs_repository_started_idx
    ON raw.pipeline_runs (repository, started_at DESC);

CREATE TABLE raw.repositories (
    id bigint PRIMARY KEY CHECK (id > 0),
    node_id text NOT NULL,
    name text NOT NULL,
    full_name text NOT NULL,
    owner text NOT NULL,
    description text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    pushed_at timestamptz,
    language text,
    stars bigint NOT NULL CHECK (stars >= 0),
    forks bigint NOT NULL CHECK (forks >= 0),
    watchers bigint NOT NULL CHECK (watchers >= 0),
    open_issues bigint NOT NULL CHECK (open_issues >= 0),
    default_branch text NOT NULL,
    archived boolean NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('public', 'private', 'internal')),
    ingested_at timestamptz NOT NULL,
    raw_path text NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id)
);
CREATE INDEX repositories_full_name_idx ON raw.repositories (full_name);
