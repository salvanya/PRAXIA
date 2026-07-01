-- ====== Tenant / práctica ======
CREATE TABLE IF NOT EXISTS practices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type IN ('clinica','odontologia','psicologia','tutoria','legal','otro')),
    settings     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    full_name    TEXT NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin','profesional','owner')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS practitioners (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id   UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    user_id       UUID REFERENCES users(id),
    full_name     TEXT NOT NULL,
    speciality    TEXT,
    working_hours JSONB DEFAULT '{}'::jsonb,
    active        BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS clients (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    full_name    TEXT NOT NULL,
    dob          DATE,
    email        TEXT,
    phone        TEXT,
    tags         JSONB DEFAULT '[]'::jsonb,
    status       TEXT NOT NULL DEFAULT 'activo' CHECK (status IN ('activo','inactivo','baja')),
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_clients_practice ON clients(practice_id);

CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id    UUID REFERENCES clients(id),
    uploaded_by  UUID REFERENCES users(id),
    doc_type     TEXT NOT NULL,
    title        TEXT NOT NULL,
    file_uri     TEXT NOT NULL,
    mime_type    TEXT,
    content_hash TEXT,
    page_count   INT,
    status       TEXT NOT NULL DEFAULT 'procesando'
                 CHECK (status IN ('procesando','indexado','error')),
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Para bases ya creadas antes de agregar content_hash (idempotente).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
-- Guardrails PII (Slice 9): resumen no-destructivo de PII por documento.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS pii_summary JSONB;
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(practice_id, doc_type);
-- Dedup de ingesta: un mismo contenido no se re-indexa dentro de la práctica.
-- Los NULL no colisionan en un índice único (filas viejas sin hash conviven).
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash
    ON documents(practice_id, content_hash);

-- ====== Turnos / citas ======
CREATE TABLE IF NOT EXISTS appointments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    practitioner_id UUID NOT NULL REFERENCES practitioners(id),
    start_at        TIMESTAMPTZ NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'programado'
                    CHECK (status IN ('programado','confirmado','atendido','ausente','cancelado')),
    reason          TEXT,
    channel         TEXT,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_appt_practice_date ON appointments(practice_id, start_at);
CREATE INDEX IF NOT EXISTS idx_appt_client ON appointments(client_id);

-- ====== Interacciones (el corazón del CRM de atención) ======
CREATE TABLE IF NOT EXISTS interactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    practitioner_id UUID REFERENCES practitioners(id),
    appointment_id  UUID REFERENCES appointments(id),
    type            TEXT NOT NULL CHECK (type IN ('sesion','llamada','email','nota','mensaje')),
    summary         TEXT,
    content         TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','agente','import')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interactions_client ON interactions(client_id, occurred_at DESC);
