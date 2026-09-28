-- Existing invalid rows cause migration rollback; never silently repair source data.
ALTER TABLE raw.pipeline_runs
    ADD COLUMN duration_ms bigint CHECK (duration_ms >= 0),
    ADD COLUMN records_skipped integer GENERATED ALWAYS AS (
        CASE WHEN status = 'SUCCESS' THEN records_extracted - records_loaded ELSE 0 END
    ) STORED,
    ADD CONSTRAINT pipeline_runs_finite_time CHECK (
        isfinite(started_at) AND (finished_at IS NULL OR isfinite(finished_at))
    ),
    ADD CONSTRAINT pipeline_runs_failed_atomic CHECK (status <> 'FAILED' OR records_loaded = 0);
UPDATE raw.pipeline_runs SET duration_ms = greatest(
    0, floor(extract(epoch FROM (finished_at - started_at)) * 1000)::bigint
) WHERE finished_at IS NOT NULL;
ALTER TABLE raw.pipeline_runs ADD CONSTRAINT pipeline_runs_duration_state CHECK (
    (status = 'RUNNING' AND duration_ms IS NULL)
    OR (status <> 'RUNNING' AND duration_ms IS NOT NULL)
);
CREATE INDEX pipeline_runs_latest_idx
    ON raw.pipeline_runs (repository, pipeline_name, started_at DESC);

ALTER TABLE raw.repositories
    ADD CONSTRAINT repositories_identity CHECK (
        btrim(node_id) <> '' AND btrim(default_branch) <> ''
        AND full_name ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$'
        AND name NOT IN ('.', '..')
        AND lower(full_name) = lower(owner || '/' || name)
    ),
    ADD CONSTRAINT repositories_valid_times CHECK (
        isfinite(created_at) AND isfinite(updated_at) AND isfinite(ingested_at)
        AND (pushed_at IS NULL OR isfinite(pushed_at)) AND updated_at >= created_at
    );

CREATE FUNCTION raw.valid_parent_shas(value text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT coalesce(bool_and(sha IS NOT NULL AND sha ~ '^[0-9a-f]{40}$'), true)
    FROM unnest(value) AS sha
$$;
ALTER TABLE raw.commits
    ADD CONSTRAINT commits_valid_parents CHECK (raw.valid_parent_shas(parent_shas)),
    ADD CONSTRAINT commits_finite_times CHECK (
        isfinite(ingested_at)
        AND (authored_at IS NULL OR isfinite(authored_at))
        AND (committed_at IS NULL OR isfinite(committed_at))
    );
ALTER TABLE raw.issues ADD CONSTRAINT issues_valid_times CHECK (
    isfinite(created_at) AND isfinite(updated_at) AND isfinite(ingested_at)
    AND updated_at >= created_at
    AND (closed_at IS NULL OR (isfinite(closed_at) AND closed_at >= created_at))
);
ALTER TABLE raw.pull_requests ADD CONSTRAINT pull_requests_valid_times CHECK (
    isfinite(created_at) AND isfinite(updated_at) AND isfinite(ingested_at)
    AND updated_at >= created_at
    AND (closed_at IS NULL OR (isfinite(closed_at) AND closed_at >= created_at))
    AND (merged_at IS NULL OR (isfinite(merged_at) AND merged_at >= created_at))
);
ALTER TABLE raw.ingestion_checkpoints
    ADD CONSTRAINT checkpoint_head_loaded FOREIGN KEY (repository_id, head_sha)
        REFERENCES raw.commits(repository_id, sha),
    ADD CONSTRAINT checkpoint_valid_branch CHECK (btrim(branch) <> '' AND isfinite(updated_at));
ALTER TABLE raw.entity_checkpoints ADD CONSTRAINT entity_checkpoint_finite CHECK (
    isfinite(watermark) AND isfinite(updated_at) AND watermark <= updated_at + interval '5 minutes'
);

-- Checked at commit because the checkpoint is written before the SUCCESS audit.
CREATE FUNCTION raw.check_checkpoint_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_pipeline text;
BEGIN
    IF TG_TABLE_NAME = 'ingestion_checkpoints' THEN
        expected_pipeline := 'commit_ingestion';
    ELSE
        expected_pipeline := NEW.entity || '_ingestion';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM raw.pipeline_runs
        WHERE id = NEW.pipeline_run_id AND status = 'SUCCESS'
          AND pipeline_name = expected_pipeline
    ) THEN
        RAISE EXCEPTION 'Checkpoint requires a successful matching pipeline run'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER commits_checkpoint_audit
AFTER INSERT OR UPDATE ON raw.ingestion_checkpoints
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION raw.check_checkpoint_audit();
CREATE CONSTRAINT TRIGGER entity_checkpoint_audit
AFTER INSERT OR UPDATE ON raw.entity_checkpoints
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION raw.check_checkpoint_audit();

ALTER TABLE raw.issues ADD CONSTRAINT issues_required_text CHECK (
    btrim(title) <> '' AND (author_login IS NULL OR btrim(author_login) <> '')
);
ALTER TABLE raw.pull_requests ADD CONSTRAINT pull_requests_required_text CHECK (
    btrim(title) <> '' AND (author_login IS NULL OR btrim(author_login) <> '')
);
