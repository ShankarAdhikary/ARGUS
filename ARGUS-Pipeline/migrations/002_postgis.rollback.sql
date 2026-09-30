-- Drops the spatial mirrors (data in them can be rebuilt by re-ingesting) but leaves the postgis extension installed.
DROP TABLE IF EXISTS sightings;
-- migrate:split
DROP TABLE IF EXISTS suspects;
-- migrate:split
DROP TABLE IF EXISTS firs;
