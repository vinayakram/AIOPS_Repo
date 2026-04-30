CREATE EXTENSION IF NOT EXISTS vector;

-- The application creates RCA knowledge tables with SQLAlchemy first, then
-- adds vector columns and indexes at startup. Keeping this init script limited
-- to the extension avoids failing on a brand-new empty Postgres database.
