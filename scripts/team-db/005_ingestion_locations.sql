-- =============================================================================
-- Reg IRIS - migration 005: source locations for chunks (ingestion)
--
-- Why: document_chunks.page_number is NOT NULL, but HTML and TXT files have no
-- pages. Filling in "page 1" would give wrong citations. The ingestion pipeline
-- already produces the real location of every chunk:
--   PDF   page_number .. page_end   (a clause can run onto the next page)
--   HTML  anchor                    (the element id, e.g. 'p4' -> #p4)
--   TXT   line_start .. line_end    (e.g. Q5 = lines 15-16)
-- plus the clause heading, and a flag for text of another document pasted
-- into this one (EMIR_2021_v2.pdf contains the whole Dodd-Frank document).
--
-- Only adds columns and relaxes one NOT NULL. Existing rows stay valid; no
-- data is changed or removed. Apply after 004 (Alembic revision 0005, or psql).
-- =============================================================================

BEGIN;

ALTER TABLE document_chunks
    ALTER COLUMN page_number DROP NOT NULL,
    ADD COLUMN page_end         INTEGER,            -- last page, when the chunk spans pages
    ADD COLUMN anchor           TEXT,               -- HTML element id, e.g. 'p4'
    ADD COLUMN line_start       INTEGER,            -- TXT first line (1-based)
    ADD COLUMN line_end         INTEGER,            -- TXT last line (inclusive)
    ADD COLUMN heading          TEXT,               -- clause / section heading
    ADD COLUMN foreign_content  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN embedded_source  TEXT,               -- e.g. 'Dodd_Frank_2022.pdf'
    ADD CONSTRAINT ck_document_chunks_page_end
        CHECK (page_end IS NULL OR (page_number IS NOT NULL AND page_end >= page_number)),
    ADD CONSTRAINT ck_document_chunks_lines
        CHECK ((line_start IS NULL) = (line_end IS NULL) AND (line_end IS NULL OR line_end >= line_start)),
    ADD CONSTRAINT ck_document_chunks_location
        CHECK (page_number IS NOT NULL OR anchor IS NOT NULL OR line_start IS NOT NULL OR heading IS NOT NULL),
    ADD CONSTRAINT ck_document_chunks_foreign
        CHECK (foreign_content = (embedded_source IS NOT NULL));

ALTER TABLE document_versions
    ADD COLUMN version_label  TEXT,                              -- e.g. 'v2.0' from doc_metadata.csv
    ADD COLUMN warnings       JSONB NOT NULL DEFAULT '[]'::jsonb; -- e.g. 'No metadata for: regulator'

COMMIT;
