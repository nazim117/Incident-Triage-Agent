-- Runs automatically the FIRST time the postgres container starts with an
-- empty data directory (Postgres's official image executes every .sql/.sh
-- file in /docker-entrypoint-initdb.d/, in filename order, exactly once).
-- It will NOT re-run on subsequent restarts as long as the `pgdata` volume
-- (see docker-compose.yml) still has data in it - if you need to re-seed,
-- you have to remove the volume first (`docker compose down -v`).
--
-- This just gives the webapp's /db-check endpoint (and later, the agent)
-- something real to query, beyond a bare "SELECT 1".

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO incidents (description) VALUES
    ('stack initialized - no incidents yet');
