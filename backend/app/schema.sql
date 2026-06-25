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
    page_count   INT,
    status       TEXT NOT NULL DEFAULT 'procesando'
                 CHECK (status IN ('procesando','indexado','error')),
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(practice_id, doc_type);
