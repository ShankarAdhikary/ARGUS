-- Phase 4: PostGIS + geography columns. Idempotent. Rollback: 002_postgis.rollback.sql
-- Chunks are separated by marker lines (see db.apply_migrations) because CREATE INDEX CONCURRENTLY cannot share a transaction.
CREATE EXTENSION IF NOT EXISTS postgis;
-- migrate:split
-- FIRs, suspects and sightings live in Elasticsearch/Neo4j; these are the spatial mirrors the analytics query.
CREATE TABLE IF NOT EXISTS firs (
    fir_id TEXT PRIMARY KEY,
    jurisdiction TEXT NOT NULL DEFAULT 'Unassigned',
    station TEXT,
    filed_on DATE,
    offense_categories TEXT[] NOT NULL DEFAULT '{}',
    is_women_safety BOOLEAN NOT NULL DEFAULT FALSE,
    geocode_level TEXT,
    geocode_confidence DOUBLE PRECISION
);
ALTER TABLE firs ADD COLUMN IF NOT EXISTS location GEOGRAPHY(POINT, 4326);

CREATE TABLE IF NOT EXISTS suspects (
    suspect_id TEXT PRIMARY KEY,
    canonical_name TEXT,
    jurisdiction TEXT,
    wsrs_score DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE suspects ADD COLUMN IF NOT EXISTS last_known_location GEOGRAPHY(POINT, 4326);

CREATE TABLE IF NOT EXISTS sightings (
    sighting_id TEXT PRIMARY KEY,
    suspect_id TEXT NOT NULL,
    camera_id TEXT,
    seen_at TIMESTAMPTZ,
    match_confidence DOUBLE PRECISION
);
ALTER TABLE sightings ADD COLUMN IF NOT EXISTS location GEOGRAPHY(POINT, 4326);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS firs_location_gix ON firs USING GIST (location);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS suspects_last_known_location_gix ON suspects USING GIST (last_known_location);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS sightings_location_gix ON sightings USING GIST (location);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS firs_jurisdiction_idx ON firs (jurisdiction);
-- migrate:split
-- After the database image (and so the PostGIS package) is upgraded, bring the extension's catalog objects up to date.
-- A no-op ("version has not changed") when it already is.
ALTER EXTENSION postgis UPDATE;
