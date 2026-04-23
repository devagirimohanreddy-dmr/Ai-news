-- =============================================================================
-- PostgreSQL initialization script
-- Runs automatically on first container start via docker-entrypoint-initdb.d
-- =============================================================================

-- Trigram index support for fuzzy text search (e.g. article deduplication)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN index support for composite indexing strategies
CREATE EXTENSION IF NOT EXISTS btree_gin;
