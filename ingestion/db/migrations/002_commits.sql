CREATE TABLE raw.commits (
    repository_id bigint NOT NULL REFERENCES raw.repositories(id),
    sha text NOT NULL CHECK (sha ~ '^[0-9a-f]{40}$'),
    message text NOT NULL,
    author_name text,
    author_email text,
    authored_at timestamptz,
    committer_name text,
    committer_email text,
    committed_at timestamptz,
    author_login text,
    committer_login text,
    parent_shas text[] NOT NULL,
    ingested_at timestamptz NOT NULL,
    raw_path text NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id),
    PRIMARY KEY (repository_id, sha)
);
CREATE INDEX commits_repository_date_idx
    ON raw.commits (repository_id, committed_at DESC);

CREATE TABLE raw.ingestion_checkpoints (
    repository_id bigint PRIMARY KEY REFERENCES raw.repositories(id),
    branch text NOT NULL,
    head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
    updated_at timestamptz NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id)
);
