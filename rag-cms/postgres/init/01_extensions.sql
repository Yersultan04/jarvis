-- Enabled automatically on container init.
-- 'unaccent' lets the FTS layer match queries regardless of diacritics.
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
