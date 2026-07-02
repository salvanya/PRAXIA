"""Seeder de datos sintéticos para el demo (CLAUDE.md §7: Faker, nada real).

Idempotente y determinístico (semilla fija). Las fechas de los turnos son
relativas a now() para que "esta semana" siempre tenga datos.

Uso: backend\\.venv\\Scripts\\python backend\\seed_demo.py
"""

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

from faker import Faker

from app import db
from app.config import get_settings
from app.eval.fixtures import ensure_rag_fixture

_NS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_APPT_STATUS = ["programado", "confirmado", "atendido", "ausente", "cancelado"]
_CLIENT_STATUS = ["activo", "activo", "activo", "inactivo", "baja"]  # sesgo a activo


def _det_uuid(label: str) -> str:
    return str(uuid.uuid5(_NS, label))


async def seed_demo() -> dict[str, int]:
    settings = get_settings()
    practice_id = settings.practice_id
    fake = Faker("es_AR")
    fake.seed_instance(42)
    rng = random.Random(42)
    pool = await db.get_pool()

    # Garantizar que la práctica demo existe (FK requerida por practitioners/clients).
    await pool.execute(
        "INSERT INTO practices (id, name, type) "
        "VALUES ($1, 'Práctica Demo', 'psicologia') ON CONFLICT (id) DO NOTHING",
        practice_id,
    )

    practitioners: list[str] = []
    for i in range(3):
        pid = _det_uuid(f"prac-{i}")
        practitioners.append(pid)
        await pool.execute(
            "INSERT INTO practitioners (id, practice_id, full_name, speciality, active) "
            "VALUES ($1, $2, $3, $4, true) ON CONFLICT (id) DO NOTHING",
            pid,
            practice_id,
            fake.name(),
            rng.choice(["Clínica", "Psicología", "Odontología"]),
        )

    clients: list[str] = []
    for i in range(30):
        cid = _det_uuid(f"client-{i}")
        clients.append(cid)
        await pool.execute(
            "INSERT INTO clients (id, practice_id, full_name, email, phone, status) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, phone = EXCLUDED.phone",
            cid,
            practice_id,
            fake.name(),
            fake.email(),
            fake.phone_number(),
            rng.choice(_CLIENT_STATUS),
        )

    # appointments: borrar y reinsertar (no referenciadas por otras tablas de este schema)
    await pool.execute("DELETE FROM appointments WHERE practice_id = $1", practice_id)
    now = datetime.now(UTC)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    starts: list[datetime] = []
    for _ in range(12):  # garantizados esta semana
        starts.append(monday + timedelta(days=rng.randint(0, 6), hours=rng.randint(8, 18)))
    for _ in range(68):  # dispersos en el pasado/futuro
        base = now + timedelta(days=rng.randint(-45, 20))
        starts.append(base.replace(hour=rng.randint(8, 18), minute=0, second=0, microsecond=0))

    for i, start in enumerate(starts):
        end = start + timedelta(minutes=rng.choice([30, 45]))
        await pool.execute(
            "INSERT INTO appointments "
            "(id, practice_id, client_id, practitioner_id, "
            "start_at, end_at, status, reason, channel) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            _det_uuid(f"appt-{i}"),
            practice_id,
            rng.choice(clients),
            rng.choice(practitioners),
            start,
            end,
            rng.choice(_APPT_STATUS),
            fake.sentence(nb_words=4),
            rng.choice(["presencial", "telellamada"]),
        )

    n_chunks = await ensure_rag_fixture()

    return {
        "practitioners": len(practitioners),
        "clients": len(clients),
        "appointments": len(starts),
        "documents": n_chunks,
    }


if __name__ == "__main__":
    print(f"seed_demo: {asyncio.run(seed_demo())}")
