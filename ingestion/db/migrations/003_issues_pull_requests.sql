CREATE TABLE raw.issues (
    id bigint PRIMARY KEY CHECK (id > 0),
    number bigint NOT NULL CHECK (number > 0),
    repository_id bigint NOT NULL REFERENCES raw.repositories(id),
    title text NOT NULL,
    state text NOT NULL CHECK (state IN ('open', 'closed')),
    author_login text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    comments_count bigint NOT NULL CHECK (comments_count >= 0),
    ingested_at timestamptz NOT NULL,
    raw_path text NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id),
    UNIQUE (repository_id, number)
);
CREATE INDEX issues_repository_updated_idx ON raw.issues (repository_id, updated_at);

CREATE TABLE raw.pull_requests (
    id bigint PRIMARY KEY CHECK (id > 0),
    number bigint NOT NULL CHECK (number > 0),
    repository_id bigint NOT NULL REFERENCES raw.repositories(id),
    title text NOT NULL,
    state text NOT NULL CHECK (state IN ('open', 'closed')),
    author_login text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    merged_at timestamptz,
    merge_commit_sha text CHECK (merge_commit_sha ~ '^[0-9a-f]{40}$'),
    draft boolean NOT NULL,
    ingested_at timestamptz NOT NULL,
    raw_path text NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id),
    UNIQUE (repository_id, number),
    CHECK (merged_at IS NULL OR state = 'closed')
);
CREATE INDEX pull_requests_repository_updated_idx
    ON raw.pull_requests (repository_id, updated_at);

CREATE TABLE raw.entity_checkpoints (
    repository_id bigint NOT NULL REFERENCES raw.repositories(id),
    entity text NOT NULL CHECK (entity IN ('issues', 'pull_requests')),
    watermark timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    pipeline_run_id uuid NOT NULL REFERENCES raw.pipeline_runs(id),
    PRIMARY KEY (repository_id, entity)
);
