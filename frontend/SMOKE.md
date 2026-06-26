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
