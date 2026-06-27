# Smoke manual — Fase 0 slice

Prerequisitos: `docker compose up -d` (Postgres+Qdrant) y Ollama corriendo con el modelo de `OLLAMA_MODEL` pulled.

1. Backend: `backend/.venv/Scripts/python backend/dev.py` (http://localhost:8000)
   - En Windows el runner `dev.py` fuerza `SelectorEventLoop` (psycopg async del checkpointer no soporta el `ProactorEventLoop` que uvicorn usa por defecto). No arranques con `python -m uvicorn` directo: crashea en el startup.
2. Frontend: `cd frontend && npm run dev` (http://localhost:3000)
3. En el navegador (http://localhost:3000):
   - Soltá `backend/tests/fixtures/protocolo.md` en la drop zone → debe pasar a `indexado` y aparecer en "Documentos".
   - Preguntá en el chat: "¿cuánto dura la primera consulta?" → respuesta en streaming, en español, mencionando "60 minutos", con un bloque **Fuentes**.
   - Preguntá algo no cubierto: "¿cuál es la dirección de la clínica?" → mensaje de abstención.

Sin Ollama: los pasos de ingesta y el streaming SSE igual funcionan; el chat devolverá el mensaje de abstención (no hay LLM para sintetizar), lo que valida todo el cableado UI↔backend salvo la síntesis real.

- **Acción de escritura (HITL):** escribí `agendá un turno para <nombre de un cliente> mañana a las 10`.
  Esperado: aparece una **tarjeta de confirmación** con el resumen del turno (cliente, profesional,
  fecha/hora) y botones **Confirmar / Cancelar**.
  - **Confirmar** → recibo `✅ Turno creado: …`. Verificá en la DB:
    `docker compose exec -T postgres psql -U praxia -d praxia -c "SELECT client_id, start_at, status FROM appointments ORDER BY created_at DESC LIMIT 1;"`
  - **Cancelar** → `Cancelado, no creé el turno.` y la tabla `appointments` no crece.
  - Pedido irresoluble (`agendá un turno para Zzz`) → abstención cordial, SIN tarjeta.
