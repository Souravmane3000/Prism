-- ============================================================================
-- 001_initial.sql — Prism complete database schema
-- Run once against your Supabase project via the SQL editor or migration tool.
-- ============================================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Enum types ─────────────────────────────────────────────────────────────────
CREATE TYPE run_status AS ENUM (
    'running',
    'awaiting_approval',
    'completed',
    'failed',
    'cancelled'
);

CREATE TYPE agent_phase AS ENUM (
    'start',
    'complete'
);

-- ── runs ───────────────────────────────────────────────────────────────────────
-- Primary record for each pipeline execution. Realtime is enabled on this table
-- so the frontend receives status/agent changes in real time.
CREATE TABLE IF NOT EXISTS runs (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url            TEXT        NOT NULL,
    issue_url           TEXT,
    issue_text          TEXT,
    github_token_hint   CHAR(4),    -- last 4 chars of PAT only; never the full token
    status              run_status  NOT NULL DEFAULT 'running',
    current_agent       TEXT        NOT NULL DEFAULT 'planner',
    error               TEXT,
    all_tests_passed    BOOLEAN,
    pr_url              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC);

-- Keep updated_at current automatically
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER runs_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── agent_outputs ──────────────────────────────────────────────────────────────
-- One row per agent lifecycle event (start + complete). Realtime enabled.
-- The frontend subscribes filtered by run_id and renders cards as rows arrive.
CREATE TABLE IF NOT EXISTS agent_outputs (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID        NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    agent       TEXT        NOT NULL,
    phase       agent_phase NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_outputs_run_id ON agent_outputs (run_id);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_run_agent ON agent_outputs (run_id, agent);

-- ── HITL checkpoints ─────────────────────────────────────────────────────────
-- Stores HITL interrupt payloads and records user decisions on resume.
-- Named hitl_checkpoints (NOT checkpoints) to avoid collision with LangGraph's
-- own checkpoints table created by AsyncPostgresSaver.setup().
-- Realtime enabled so the frontend can react when a checkpoint row appears.
CREATE TABLE IF NOT EXISTS hitl_checkpoints (
    id                UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id            UUID        NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    checkpoint_name   TEXT        NOT NULL,   -- "hitl_1" | "hitl_2"
    payload           JSONB       NOT NULL DEFAULT '{}',
    user_decision     JSONB,                  -- NULL until resolved
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hitl_checkpoints_run_id ON hitl_checkpoints (run_id);

-- ── repo_cache ─────────────────────────────────────────────────────────────────
-- Caches file tree and embedding counts per repo to avoid re-embedding on
-- every run against the same repository.
CREATE TABLE IF NOT EXISTS repo_cache (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url        TEXT        NOT NULL UNIQUE,
    file_tree       JSONB       NOT NULL DEFAULT '[]',
    embedding_count INTEGER     NOT NULL DEFAULT 0,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repo_cache_url ON repo_cache (repo_url);

-- ── code_embeddings ────────────────────────────────────────────────────────────
-- pgvector-backed semantic index of repository file chunks.
-- vector(1536) matches text-embedding-3-small / compatible embedding size.
CREATE TABLE IF NOT EXISTS code_embeddings (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url    TEXT        NOT NULL,
    file_path   TEXT        NOT NULL,
    chunk_index INTEGER     NOT NULL,
    chunk_text  TEXT        NOT NULL,
    embedding   vector(1536),
    token_count INTEGER,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (repo_url, file_path, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_code_embeddings_repo ON code_embeddings (repo_url);

-- IVFFlat index for approximate nearest-neighbour cosine similarity search.
-- lists=100 is a reasonable starting point; tune for dataset size.
CREATE INDEX IF NOT EXISTS idx_code_embeddings_vector
    ON code_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── RPC: match_code_embeddings ─────────────────────────────────────────────────
-- Called by supabase_client.search_code_embeddings() to perform semantic search.
CREATE OR REPLACE FUNCTION match_code_embeddings(
    query_embedding vector(1536),
    match_repo_url  TEXT,
    match_count     INT DEFAULT 10
)
RETURNS TABLE (
    id          UUID,
    repo_url    TEXT,
    file_path   TEXT,
    chunk_index INTEGER,
    chunk_text  TEXT,
    token_count INTEGER,
    metadata    JSONB,
    similarity  FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ce.id,
        ce.repo_url,
        ce.file_path,
        ce.chunk_index,
        ce.chunk_text,
        ce.token_count,
        ce.metadata,
        1 - (ce.embedding <=> query_embedding) AS similarity
    FROM code_embeddings ce
    WHERE ce.repo_url = match_repo_url
      AND ce.embedding IS NOT NULL
    ORDER BY ce.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ── Supabase Realtime ──────────────────────────────────────────────────────────
-- Enable Realtime broadcasting on the three tables the frontend subscribes to.
-- Run in Supabase SQL editor; requires Realtime to be enabled in project settings.
ALTER PUBLICATION supabase_realtime ADD TABLE runs;
ALTER PUBLICATION supabase_realtime ADD TABLE agent_outputs;
ALTER PUBLICATION supabase_realtime ADD TABLE hitl_checkpoints;
