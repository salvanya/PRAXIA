# Praxia · Fase 1 · Slice 1 — Esqueleto del grafo LangGraph + router semántico

> Diseño aprobado el 2026-06-25. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.

## Objetivo

Reemplazar el flujo RAG plano de Fase 0 (`/chat` → `retrieve` → `synthesize`) por una
**máquina de estados LangGraph** con un **router semántico** que clasifica la intención y
deriva al subgrafo correspondiente. Es el esqueleto del que colgarán, en slices posteriores,
CRAG (jueces + reranker), Data Agent NL2SQL, tools de escritura con human-in-the-loop,
memoria y guardrails.

**No-objetivos de este slice** (cada uno es un slice propio, posterior):
- CRAG: reranker `bge-reranker-v2-m3` + jueces de relevancia/groundedness.
- NL2SQL real: capa semántica + generación de `SELECT` + juez intención↔SQL.
- Tools de escritura + `interrupt` (human-in-the-loop).
- Guardrails (Presidio PII, inyección).
- Continuidad real de memoria de corto plazo en el frontend (plumbing de `thread_id` persistente).

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Toda inferencia por Ollama local.
El grafo es la fuente de control (CLAUDE.md §4): toda entrada pasa por el router antes de
cualquier subgrafo. No se agregan caminos que esquiven el router.

## Arquitectura

### Módulos nuevos (`backend/app/graph/`, CLAUDE.md §3)

```
app/graph/
├── __init__.py
├── state.py     # AgentState (TypedDict) — tipado y mínimo
├── router.py    # router LLM e4b + salida estructurada → intent
├── nodes.py     # funciones puras: rag_node, chitchat_node, scope_reject_node, sql_stub, action_stub
├── edges.py     # routing condicional desde el router hacia el subgrafo
└── build.py     # ensambla el StateGraph + checkpointer; expone get_graph()
```

- `app/rag/` **NO se modifica**. `retrieve()` y `synthesize_stream()` quedan idénticos;
  `rag_node` los envuelve.
- **Regla CLAUDE.md §3**: un nodo = una función pura testeable en `nodes.py`. La lógica de
  negocio sigue en `agents/`/`rag/`, no en los nodos.

### Flujo de control

```
/chat → (probe Ollama → 503 amable si caído) → grafo:
        START → router → {rag | chitchat | scope_reject | sql_stub | action_stub} → END
```

## `AgentState` (state.py)

Tipado y mínimo (CLAUDE.md §4). Se **declaran** campos futuros pero solo se **llenan** los de
este slice; el resto los completan sus slices.

```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from app.models import Chunk

class AgentState(TypedDict):
    # --- usados en este slice ---
    messages: Annotated[list, add_messages]   # historial; lo persiste el checkpointer
    practice_id: str
    thread_id: str
    intent: str                                # rag|sql|action|chitchat|out_of_scope
    retrieved: list[Chunk]                     # salida de retrieve()
    sources: list[dict]                        # build_sources() para el evento SSE
    # --- declarados, se llenan en slices posteriores (CLAUDE.md §4) ---
    # plan, candidate_sql, proposed_action, judge_scores, memories, running_summary
```

Decisión: los campos futuros se documentan en comentario; **no** se agregan al TypedDict hasta
que su slice los use, para que el state se mantenga chico y mypy no tolere campos sin escritor.

## Router semántico (router.py)

- Modelo: **`gemma4:e4b`** (CLAUDE.md §1: "LLM barato para router/jueces").
- **Salida estructurada obligatoria** (CLAUDE.md §4): `ChatOllama(model="gemma4:e4b", ...)`
  con decodificación restringida (JSON Schema de Ollama vía `.with_structured_output(schema)`).
  Prohibido parsear el intent con regex.
- Schema de salida: `{"intent": Literal["rag","sql","action","chitchat","out_of_scope"]}`.
- Prompt: system estable que describe cada intent con 1-2 ejemplos en español. Sin streaming
  (es una decisión interna, no va al usuario).
- Entrada: el último mensaje humano de `state["messages"]`. Salida: setea `state["intent"]`.

Mapa intent → nodo:
| intent | nodo | comportamiento |
|---|---|---|
| `rag` | rag_node | retrieve + synthesize con citas (lo de hoy) |
| `chitchat` | chitchat_node | respuesta breve con 12b, `sources=[]` |
| `sql` | sql_stub | mensaje "no disponible (próximo slice)" |
| `action` | action_stub | mensaje "no disponible (próximo slice)" |
| `out_of_scope` | scope_reject_node | respuesta segura fija, sin LLM |

## Nodos (nodes.py)

Cada nodo es `async def node(state: AgentState) -> dict` (devuelve el parche de state).

- **rag_node**: `chunks = await retrieve(query, practice_id=state["practice_id"])`.
  Si hay chunks → `synthesize_stream` emite tokens (vía el mecanismo de streaming del grafo,
  ver abajo) y setea `sources = build_sources(chunks)`. Si no hay → abstención
  (`ABSTAIN_MESSAGE`), `sources=[]`. Comportamiento idéntico a Fase 0.
- **chitchat_node**: respuesta breve y cordial con `gemma4:12b` (saludos/charla trivial).
  `sources=[]`. Streamea tokens.
- **scope_reject_node**: respuesta segura fija (sin LLM) para lo fuera de alcance
  (CLAUDE.md §5, "scope guardrail"). `sources=[]`.
- **sql_stub / action_stub**: rutean correctamente pero responden
  "Esa función todavía no está disponible (próximo slice)." `sources=[]`. **No escriben nada**
  (mantiene DoD §6.3: las escrituras siguen sin existir / pidiendo confirmación).

## Streaming SSE a través del grafo

El contrato SSE actual del frontend (`event: token` / `event: sources` / `event: done`) se
**mantiene idéntico** para no romper el front.

- `/chat` invoca `graph.astream_events(input, config, version="v2")`.
- Reenvía como `event: token` únicamente los eventos `on_chat_model_stream` cuyo
  `metadata["langgraph_node"]` ∈ {`rag`, `chitchat`, `sql_stub`, `action_stub`,
  `scope_reject`}. Así los tokens del **LLM del router NO** se filtran al usuario.
- Al cerrar el stream: lee el state final (último evento / `graph.aget_state`) y emite
  `event: sources` con `state["sources"]` y luego `event: done` `[DONE]`.
- El probe `ollama_available()` se mantiene en el endpoint **antes** de invocar el grafo
  (503 amable si Ollama está caído), preservando el fix de la limpieza pre-Fase 1.

## Checkpointer (Opción 2 — decisión registrada)

- Se monta `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`) y se corre `.setup()`
  (crea sus tablas) en el `lifespan` de FastAPI.
- El `thread_id` se genera **server-side por request** en este slice (`uuid4`). **No** hay
  continuidad real de conversación todavía.
- **Razón**: la continuidad solo aporta valor cuando el frontend manda un `thread_id` estable,
  y ese plumbing está explícitamente diferido al "canvas más rico" de Fase 1 (junto a la
  migración de `<Thread>`). Montar la infra ahora deja el grafo cableado de una vez —no se
  vuelve a tocar el grafo cuando el front mande el `thread_id`. La alternativa (cablear el
  contrato de `/chat` + front ahora) arrastraría trabajo de frontend a un slice de backend.
- `ChatRequest` **no cambia** en este slice (sigue siendo `{message}`).

## API

`/chat` (POST) — contrato externo sin cambios:
- Request: `{ "message": str }`.
- Response: `EventSourceResponse` con eventos `token` / `sources` / `done` (idéntico a Fase 0).
- Internamente: probe Ollama → arma `AgentState` inicial (`practice_id` de settings,
  `thread_id` uuid4, `messages=[HumanMessage(message)]`) → `astream_events` del grafo.

## Multi-tenant

`practice_id` viene de settings (single-tenant en dev, ya con TODO de Fase 4 en `config.py`).
`rag_node` filtra `retrieve` por `practice_id` del state (CLAUDE.md §0.5). El stub SQL no
ejecuta queries, así que no hay riesgo de fuga en este slice.

## Testing

- `tests/test_router.py`:
  - Unit: con el LLM estructurado mockeado, cada caso de entrada produce el intent esperado
    (`"hola"`→chitchat, consulta documental→rag, `"¿cuántos turnos esta semana?"`→sql,
    pregunta fuera de dominio→out_of_scope).
  - `-m llm`: 1 caso real contra `gemma4:e4b` (clasifica `"hola"`→chitchat).
- `tests/test_graph.py`:
  - El grafo rutea cada intent al nodo correcto (router mockeado).
  - `sql_stub`/`action_stub` devuelven el mensaje de "no disponible" y `sources=[]`.
  - `out_of_scope` → respuesta segura fija.
  - `rag` con chunks → setea `sources`; sin chunks → abstención.
- `tests/test_chat_sse.py` (adaptar el existente):
  - El smoke de CLAUDE.md §2 sigue verde por el grafo: `hola`→chitchat (sin citas),
    consulta documental→rag con citas en el evento `sources`, estructurada→stub SQL.
  - El 503 amable con Ollama caído se mantiene.

## Dependencias

Agregar a `backend/requirements.txt`:
- `langgraph` (núcleo de la máquina de estados).
- `langgraph-checkpoint-postgres` (checkpointer; arrastra `psycopg`).

Sin servicios cloud, sin APIs pagas, sin red saliente fuera de Ollama/Postgres/Qdrant locales
(CLAUDE.md §0, DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff check . && ruff format .`, `mypy app/` y `pytest -q` verdes.
2. Smoke de §2 pasa por el grafo: chitchat / RAG con citas / stub estructurado; el 503 amable
   sobrevive.
3. Tocamos grafo y routing: las escrituras **siguen sin existir** (los stubs no escriben);
   cuando se agreguen tools, irán detrás de `interrupt`.
4. Sin red saliente fuera de Ollama/PG/Qdrant.
5. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Streaming por `astream_events` v2**: es la parte delicada. Mitigación: filtrar por
  `langgraph_node` y cubrir con `test_chat_sse.py` que el contrato `token`/`sources`/`done`
  no cambió.
- **Fragilidad del 12b/e4b en salida estructurada** (CLAUDE.md §9): el router usa
  decodificación restringida (no regex). Si e4b clasifica mal casos límite, se documentan en
  el golden set y se ajusta el prompt (DSPy queda para Fase 2).
- **`.setup()` del checkpointer** en cada arranque: es idempotente; correrlo en el lifespan.
