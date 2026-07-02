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

- **Acción de escritura (HITL):** escribí `agendá un turno para <nombre de un cliente> con <nombre de un profesional> mañana a las 10`.
  El seed demo crea 3 profesionales activos (e.g. Amparo Paez Rodriguez, Benjamin Perez Diaz, Martina Gomez) — **nombrá uno** en el mensaje.
  Esperado: aparece una **tarjeta de confirmación** con el resumen del turno (cliente, profesional,
  fecha/hora) y botones **Confirmar / Cancelar**.
  - **Confirmar** → recibo `✅ Turno creado: …`. Verificá en la DB:
    `docker compose exec -T postgres psql -U praxia -d praxia -c "SELECT client_id, start_at, status FROM appointments ORDER BY created_at DESC LIMIT 1;"`
  - **Cancelar** → `Cancelado, no creé el turno.` y la tabla `appointments` no crece.
  - Si omitís el profesional (ej. `agendá un turno para <cliente> mañana a las 10`) el asistente **no abre tarjeta** y responde cordialmente preguntando `¿Con qué profesional? Tenés: …` — comportamiento correcto, no un error.
  - Pedido irresoluble (`agendá un turno para Zzz`) → abstención cordial, SIN tarjeta.

---

## Canvas rico (Slice canvas más rico — cierre Fase 1)

Requiere seed demo (`backend/.venv/Scripts/python backend/seed_demo.py`) + Ollama. Todos los artefactos se
renderizan **inline** en el flujo del chat vía content-parts `tool-call` de assistant-ui.

1. **Citas RAG (Citations).** Preguntá algo documental: `¿cuánto dura la primera consulta?`
   Esperado: respuesta en streaming **seguida de un bloque "Fuentes"** con footnotes numeradas
   `[1] Título — p.N` (componente estilado, **no** el markdown crudo `**Fuentes:**` con asteriscos).

2. **Tabla SQL (SqlTable).** Consulta que devuelva varias filas: `¿qué clientes tengo?` o
   `listame los turnos de esta semana`.
   Esperado: una frase breve **+ una tabla estilada** (header sticky, filas alternadas, scroll) y un
   toggle **"ver consulta"** que muestra el `SELECT`. Una consulta **escalar** (`¿cuántos turnos esta
   semana?`) muestra **solo la frase**, sin tabla.

3. **ConfirmCard por-kind (HITL).** Pedí una escritura (ver arriba: `agendá un turno para …`).
   Esperado: **tarjeta rica** con título por acción ("Agendar turno") y **campos legibles**
   (Cliente / Profesional / Cuándo …) — **sin IDs internos**. **Confirmar** → recibo; **Cancelar** →
   la acción no ocurre. La cancelación de turno (`cancelá el turno de …`) usa **tarjeta roja
   (destructiva)**. La confirmación **sigue siendo obligatoria** (HITL airtight).

4. **Chitchat.** `hola` → respuesta breve, **sin ningún artefacto** (ni tabla, ni citas, ni tarjeta).

> Si un artefacto **no aparece** (mensaje sin tabla/citas/tarjeta, o un placeholder de "tool"):
> es el render del content-part `tool-call`. Ya se aplicó el hedge `result` + `status:complete` en
> `lib/messageParts.ts` (`toContent`). Si aún fallara, revisar que los Tool UIs estén registrados en
> `<Thread tools={[…]}>` (`app/page.tsx`) y los `toolName` (`praxia_sources` / `praxia_sql_table` /
> `praxia_confirm`) coincidan entre el reducer y `components/toolUIs.tsx`.
