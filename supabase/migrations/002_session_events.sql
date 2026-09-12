-- 002_session_events.sql — Phase 4 Event Adapter durable log.
-- Ordered execution events keyed by run_id. Realtime enabled.

CREATE TABLE IF NOT EXISTS session_events (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID        NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    sequence    BIGSERIAL   NOT NULL,
    event_type  TEXT        NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_events_run_seq
    ON session_events (run_id, sequence);

ALTER PUBLICATION supabase_realtime ADD TABLE session_events;
