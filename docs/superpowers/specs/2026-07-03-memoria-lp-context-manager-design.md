# Diseño — Memoria de largo plazo + reflexión + Context Manager (mínimo)

> **Fase 2 · Slice 2** · Fecha: 2026-07-03 · Estado: **aprobado (brainstorming)**, pendiente de `writing-plans`.
> Contrato: CLAUDE.md (local-first, $0, multi-tenant por `practice_id`, escrituras solo por HITL, commits sin atribución a Claude). Diseño de referencia: `Praxia_Blueprint.md` §3.2, §4.2, §5.2.

## 1. Contexto y problema

Hoy Praxia solo tiene **memoria de corto plazo** (historial `messages` + checkpointer Postgres por `thread_id`), y **solo `chitchat`** pasa historial al LLM (`_history_messages`, window=10). `sql` y `rag` son single-turn.

**Pain destapado en el Slice 8:** el usuario dice algo en un turno (p. ej. "mi profesional es la Dra. Gómez") y luego pregunta "¿quién es mi profesional?", que **rutea a `sql`** — sin acceso al historial de chitchat ni a nada persistente → el Data Agent inventa. No hay forma de que Praxia "aprenda" hechos/preferencias de la práctica y los use en **todos** los caminos.

**Estado del código (greenfield):**
- No existe `backend/app/memory/`.
- No existe tabla `memories` en `schema.sql` (solo 7 tablas; la DDL del blueprint §5.2 es aspiracional).
- `AgentState` no tiene `memories` ni `running_summary` (`state.py:12-14` dice explícitamente que se agregan "cuando su slice los escriba" — **es esta slice**).
- No hay nodo de salida (`G_OUT`) ni de reflexión (`REFLECT`) en el grafo; el flujo aspiracional del blueprint (`G_OUT → REFLECT → END`) no está cableado.

## 2. Alcance

**En scope:**
- Tabla `memories` (Postgres, fuente de verdad) + colección Qdrant `praxia_memories` (vectores).
- Store/recall semántico por coseno, **scope `practice`**, filtrado por `practice_id`.
- Nodo `recall` (llena `state["memories"]`) + nodo `reflect` (gate e4b + extracción e4b + dedup + best-effort) re-cableado a los terminales de contenido.
- Escritura por **auto-reflexión gateada + comando explícito** ("acordate que…").
- Inyección de memorias en `chitchat`, `rag.synthesize` y `sql_agent` vía helper `context.py` (Context Manager **mínimo**).
- `AgentState.memories`.
- Tests (unit + node + 1 e2e-llm que reproduce y arregla el pain del Slice 8) + 1 caso en el golden set del eval-gate con siembra propia.

**Fuera de scope (slices siguientes; se dejan seams enganchables):**
- `running_summary`, presupuesto de tokens, refactor de prefijo-estable/KV-cache → **slice "Context Manager" siguiente**. (No se agrega el campo `running_summary` al state todavía: sin campo muerto.)
- Escritura de memorias `client`/`user`-scope + redacción PII (Presidio) de memorias → la columna `scope`/`client_id`/`user_id` queda en la tabla, pero **no se escribe**.
- Nodo de guardrails de salida `G_OUT` → **slice de guardrails endurecidos**; el seam de `reflect` queda listo para que `G_OUT` encaje **antes** de `reflect`.
- Compilar los prompts nuevos (gate/extract/inyección) con **DSPy** → slice posterior; el eval-gate deja la métrica lista para medir la mejora.
- Ejecución en **background** de la reflexión (optimización de latencia) → documentada como fast-follow.
- Inyección de memorias en `propose_action` (su prompt ya es dinámico → "turnos de 30 min" podría fijar la duración por defecto) → fast-follow opcional.

## 3. Criterios de éxito (medibles) / DoD

1. **Pain Slice-8 arreglado:** turno 1 "acordate que los turnos duran 30 minutos" (chitchat) → reflect persiste una memoria practice-scope; turno 2 **en un thread NUEVO, misma práctica**, con una consulta que rutea a `sql`/`rag` → la memoria se inyecta en el contexto y la respuesta la refleja. (El thread nuevo prueba que es **largo plazo cross-thread**, no el checkpointer.)
2. `pytest -m "not llm"` sigue verde; nuevos tests unit/node pasan; `-m eval` no regresiona con 1 caso de memoria nuevo (con siembra propia).
3. `ruff` + `mypy` verdes.
4. Smoke: las **escrituras siguen pidiendo confirmación** (memoria no toca el flujo HITL).
5. **Cero red saliente nueva** (todo Ollama/Qdrant/PG local). Todo recall/store/dedup filtra `practice_id`.

## 4. Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección | Razón |
|---|---|---|
| Tamaño de slice | Memoria LP **end-to-end**, Context Manager **mínimo** | Slice más chico que arregla el pain; difiere lo pesado. |
| Scope de memoria | **Solo `practice`** | Evita resolución de "cliente en foco" (no existe en el state) y manejo de PII. Cubre los ejemplos canónicos del blueprint. |
| Escritura | **Auto-reflexión gateada + comando explícito** | Matchea blueprint; gate+dedup evitan contaminación. |
| Integración en grafo | **Enfoque A — nodos dedicados** (`recall`, `reflect`) | "Un nodo = un propósito"; in-graph, testeable; blueprint-aligned. |
| Almacenamiento | **Postgres (verdad) + Qdrant (vectores)** | Calca `praxia_chunks`; `content` en payload → recall sin join. Descarta pgvector / PG-solo. |

## 5. Modelo de datos

### 5.1 Tabla `memories` (nueva en `schema.sql`)
```sql
-- ====== Memoria de largo plazo (semántica/episódica) ======
CREATE TABLE IF NOT EXISTS memories (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    scope        TEXT NOT NULL DEFAULT 'practice' CHECK (scope IN ('practice','client','user')),
    client_id    UUID REFERENCES clients(id),          -- null en este slice
    user_id      UUID REFERENCES users(id),            -- null en este slice
    kind         TEXT NOT NULL CHECK (kind IN ('preferencia','hecho','episodica')),
    content      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'reflexion' CHECK (source IN ('reflexion','explicito')),
    salience     REAL NOT NULL DEFAULT 0.5,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_memories_practice ON memories(practice_id, scope);
```
- Sigue la DDL del blueprint §5.2 con **una adición justificada**: `source ∈ {reflexion, explicito}` (distingue memoria automática de la pedida; útil para salience/debug/tests).
- **Sin** columna `embedding` (los vectores viven en Qdrant, como el blueprint).
- `practice_id`/`client_id`/`user_id` son `UUID` (alineado con el resto de `schema.sql`); en Python llegan como `str` (Postgres castea).

### 5.2 Colección Qdrant `praxia_memories` (calca `praxia_chunks`)
- **1024 dims, COSINE**; embeddings con **bge-m3** (`embeddings.embed_query`/`embed_texts`).
- **Point id = UUID de la memoria** (PG y Qdrant sincronizados; borrar va a ambos).
- Payload: `{practice_id, scope, kind, content, salience, created_at}` → el recall devuelve `content` desde el payload, **sin joinear PG** en el camino caliente.
- Recall: `search` con `Filter(must=[practice_id, scope='practice'])`, `limit=memory_top_k`, piso `memory_min_score`.

## 6. Arquitectura / componentes

### 6.1 Módulos nuevos
| Archivo | Responsabilidad (única) |
|---|---|
| `app/memory/__init__.py` | paquete |
| `app/memory/long_term.py` | **Capa de datos.** `store(practice_id, candidate) -> id`, `recall(query, practice_id) -> list[dict]`, `dedup(practice_id, content_vec) -> hit \| None`, `touch_last_used(ids)`, `ensure_memories_collection()`. Ops Qdrant memory-específicas (payload propio) usando el **cliente compartido** de `vectorstore.get_client()`; embeddings vía `embeddings.py`; filas PG vía `db.py`. |
| `app/memory/reflect.py` | **Cognición.** `gate(user_text, assistant_text) -> GateVerdict` (e4b), `extract(user_text, assistant_text) -> list[MemoryCandidate]` (e4b structured), orquestación `run(state) ` = guard → gate → extract → dedup → store, best-effort + timeout. |
| `app/context.py` | **Context Manager mínimo.** `format_memories_block(memories: list[dict]) -> str` (bloque de system message; `""` si vacío). Futuro hogar de `running_summary`/presupuesto/prefijo-estable. |
| `app/graph/memory_nodes.py` | Nodos finos `recall_node(state) -> dict` y `reflect_node(state) -> dict` (glue grafo↔memoria). Mantiene `nodes.py` enfocado. |

### 6.2 Módulos modificados
- `app/graph/state.py` → `AgentState.memories: list[dict]` + init `[]` en `new_state()`. (No se agrega `running_summary`.)
- `app/graph/build.py` + `edges.py` → registrar `recall`/`reflect`; re-cablear edges (§7).
- `app/graph/nodes.py` (`chitchat_node`), `app/rag/synthesize.py`, `app/agents/sql_agent.py` → inyectar `mem_block` (§7.2).
- `app/vectorstore.py` → **cambio chico**: exponer `get_client()` público (hoy `_get_client()` privado) para reusar el singleton `AsyncQdrantClient`. **No** se generalizan `search`/`upsert_chunks` (son Chunk-shaped); las ops de memoria viven en `long_term.py`.
- `app/config.py` → config nueva (§9).
- `app/schema.sql` → tabla `memories` + índice (§5.1).
- `app/main.py` → llamar `long_term.ensure_memories_collection()` en el `lifespan` (junto a `ensure_collection()` de chunks).
- `app/seed_demo.py` *(opcional)* → sembrar 2-3 memorias demo de práctica para dar señal al smoke/eval.

## 7. Flujo del grafo

### 7.1 Topología (antes → después)
**Antes:**
```
START ─entry_route─▶ (pending_clarification? clarify : router)
router ─route─▶ {rag, chitchat, scope_reject, sql_node, propose_action}
propose_action ─route_after_propose─▶ {confirm_action | END}
clarify        ─route_after_propose─▶ {confirm_action | END}
{rag, chitchat, scope_reject, sql_node, confirm_action} ─▶ END
```
**Después** (cambios marcados):
```
START ─entry_route─▶ (pending_clarification? clarify : router)
router ─▶ recall                                   ◀ NUEVO (edge directo)
recall  ─route─▶ {rag, chitchat, scope_reject, sql_node, propose_action}   ◀ 'route' se mueve a recall
propose_action ─route_after_propose─▶ {confirm_action | reflect}   ◀ END→reflect
clarify        ─route_after_propose─▶ {confirm_action | reflect}   ◀ END→reflect
{rag, chitchat, sql_node, confirm_action} ─▶ reflect               ◀ antes iban a END
scope_reject ─▶ END                                ◀ NO pasa por reflect
reflect ─▶ END                                     ◀ NUEVO
```

**Decisiones de cableado (deliberadas):**
- `recall` es nodo propio **después de `router`** (no se fusiona): router es prompt de alto apalancamiento (DSPy después) → se deja puro. Recall es barato (1 embed + 1 search).
- `scope_reject → END` directo (salta reflect): un rechazo fuera de alcance nunca es memorable → evita una llamada e4b del gate por rechazo.
- La rama `clarify` (continuación de acción pausada) **no** pasa por `recall` → en ese turno `state["memories"]=[]`. Es disambiguación mid-acción; igual llega a `reflect` vía `route_after_propose`. Consciente, no es hueco.
- El `interrupt` de `confirm_action` no se ve afectado: en el turno de la tarjeta el grafo se suspende **dentro** de confirm_action (no llega a reflect). Al confirmar (`Command(resume)`), confirm_action completa el write → reflect → END. La reflexión solo corre tras el write real.

### 7.2 Puntos de inyección (data flow)
`recall_node` llena `state["memories"]`. Cada nodo LLM de síntesis antepone `context.format_memories_block(state["memories"])` **como un system message aparte, DESPUÉS del prompt estable** (deja el prefijo estable intacto para el KV-cache de la slice siguiente):

| Nodo | Ubicación | Cómo |
|---|---|---|
| `chitchat_node` | `nodes.py:~92` | `[("system", CHITCHAT_SYSTEM), ("system", mem_block), *history]` si `mem_block≠""` |
| `rag.synthesize` | `synthesize.py:~64` | agrega `("system", mem_block)` antes del human con fragmentos |
| `sql_agent.answer` | `sql_agent.py:~190` | inyecta `mem_block` en el contexto de **síntesis** (NL). **No** toca el SELECT: sigue saliendo de la capa semántica read-only. |

- `router`, `reformulate`, `judges` **no** reciben memorias (routing/pasos internos: ruido).

### 7.3 `recall_node`
`query = last_user_text(state)` → `long_term.recall(query, practice_id)` (top-k, piso de score) → best-effort `touch_last_used(ids)` (PG) → retorna `{"memories": recalled}`. Ante fallo → `{"memories": []}` + log. Respeta `memory_recall_enabled`.

## 8. Algoritmo de reflexión (`reflect_node` → `memory/reflect.py`)

Corre tras un terminal de contenido (`rag`/`chitchat`/`sql_node`/`confirm_action`) con el state completo (incluye el `AIMessage` recién producido).

- **Paso 0 — Guard.** Si `memory_reflect_enabled=False` → `{}`. `user_text=last_user_text(state)`, `assistant_text=` último AIMessage. Si alguno vacío → `{}`.
- **Paso 1 — Gate (e4b, structured).** Un solo llamado:
  ```python
  class GateVerdict(BaseModel):        # sin underscore (gotcha Gemma structured-output)
      worth_remembering: bool
      is_explicit: bool                # "acordate que…", "recordá que…", "tené en cuenta que…"
      reason: str
  ```
  `True` **solo** para hechos/preferencias **duraderos y a nivel práctica** (glosario/terminología, reglas de agenda, políticas). `False` para saludos, preguntas one-off, contexto efímero, y **cualquier cosa client-specific/PII** (fuera de scope). **Sesgo a `False`** (precisión > recall). Si `False` → `{}` (caso común).
- **Paso 2 — Extract (e4b, structured, solo si gate pasó).**
  ```python
  class MemoryCandidate(BaseModel):
      kind: Literal["preferencia","hecho","episodica"]
      content: str                     # atómico, autocontenido, normalizado, ≤~200 chars, español
  class ExtractedMemories(BaseModel):
      memories: list[MemoryCandidate]  # cap = memory_reflect_max_candidates (3)
  ```
  Sin pronombres/dependencias de contexto ("Los turnos duran 30 minutos."). Si explícito, prioriza lo pedido. **Gotcha e4b None**: reintento ≤2x (patrón `router.py:37-41`); si sigue None → best-effort abort (`{}`).
- **Paso 3 — Dedup (por candidato).** Embebo `content` → `dedup(practice_id, vec)` = search top-1 (mismo filtro). Si score ≥ `memory_dedup_threshold` (0.9) → **duplicado**: no inserto, `touch_last_used` (solo actualiza `last_used_at`; salience-weighting = futuro). Si no → nuevo.
- **Paso 4 — Store.** Por candidato nuevo: `id=uuid4()`. **PG insert primero** (practice_id, scope='practice', kind, content, `source`='explicito'/'reflexion', `salience`=0.8/0.5), **luego Qdrant upsert** (id=uuid, vector, payload con `content`). Si Qdrant falla tras el insert → **compenso borrando la fila PG** + log (nunca PG-sin-vector = invisible al recall).
- **Paso 5 — Return `{}`.** No toca `messages` ni emite tokens. El "dale, lo tengo en cuenta" ante "acordate que…" **sale del propio chitchat**; reflect persiste en silencio. (Confirmación explícita = fast-follow.)

**Modelos:** gate+extract en **e4b** (`ollama_model_cheap`); embeddings bge-m3. Ningún 12b en reflexión. **Ranking de recall = coseno puro** con piso+top_k; salience/recency-weighting y decay = futuro (YAGNI).

## 9. Configuración nueva (`config.py`)
| Campo | Default | Uso |
|---|---|---|
| `ollama_model_cheap: str` | `"gemma4:e4b"` | gate/extract de reflect (consolida el literal e4b hoy hardcodeado; no migra los usos existentes) |
| `qdrant_memories_collection: ClassVar[str]` | `"praxia_memories"` | colección de memorias |
| `memory_recall_enabled: bool` | `True` | kill switch recall |
| `memory_reflect_enabled: bool` | `True` | kill switch reflexión (tests/prod) |
| `memory_top_k: int` | `5` | memorias recuperadas por turno |
| `memory_min_score: float` | `0.5` | piso de coseno en recall |
| `memory_dedup_threshold: float` | `0.9` | umbral de duplicado |
| `memory_reflect_max_candidates: int` | `3` | cap de memorias por turno |
| `memory_reflect_timeout_s: float` | `10.0` | timeout de gate+extract |

## 10. Errores / resiliencia / privacidad / seguridad

- **Best-effort en todo el camino de memoria.** `recall_node` y `reflect_node` envuelven su trabajo en try/except: ante cualquier fallo (Qdrant caído, Ollama timeout, e4b None, PG error) → log warning + `{}`/`{"memories": []}`. **El turno del usuario nunca se rompe por la memoria.** Regla cardinal.
- **Timeouts.** Gate/extract con `asyncio.wait_for(timeout=memory_reflect_timeout_s)`.
- **Kill switches.** `memory_reflect_enabled` / `memory_recall_enabled`.
- **Multi-tenant (innegociable).** Todo recall y todo dedup filtran `Filter(must=[practice_id, scope='practice'])`; todo store escribe `practice_id`. Test: práctica B nunca recupera memoria de práctica A.
- **PII / privacidad.** Scope=práctica → memorias operativas/terminológicas (bajo PII por construcción). El gate **rechaza contenido client-specific/PII** (scope-guard + PII-guard). Sin maquinaria PII nueva; redacción Presidio + client-scope = diferido.
- **Inyección de prompt (limitación conocida).** Un doc malicioso podría intentar plantar una memoria. Mitigación: gate sesgado a `False` + practice-level; lo guardado es un **hecho destilado**, no una instrucción; se inyecta como **contexto** ("cosas que sabés de la práctica"), **no como reglas de sistema**. Hardening completo (llm-guard) = slice de guardrails; se deja el seam.
- **Latencia.** `reflect` corre in-graph tras streamear la respuesta, pero antes de `END` → agrega ~1 llamada e4b (gate) al cierre del turno. Mitigado con gate barato + timeout + `worth_remembering=False` corta temprano. Ejecución en background = fast-follow documentado.

## 11. Testing + eval gate

- **Unit** (`tests/test_long_term_memory.py`, `-m "not llm"`; fake embeddings + Qdrant + PG docker): store↔recall round-trip; aislamiento por `practice_id` (B no ve A); dedup no duplica y toca `last_used_at`; recall respeta `min_score`/`top_k`; `format_memories_block([])==""` y no-vacío renderiza el bloque.
- **Node** (`tests/test_memory_nodes.py`, `-m "not llm"`; fake LLM): `recall_node` llena `state["memories"]`; `reflect_node` gate=False→no escribe, gate=True+extract→store con candidato esperado, store levanta excepción→node devuelve `{}` sin propagar; **inyección**: el fake LLM de chitchat/synthesize "ve" el `mem_block`; **build/edges**: asserts de cableado (`router→recall`, terminales→`reflect`, `scope_reject→END`, `reflect→END`).
- **e2e-llm** (`tests/test_memory_e2e_llm.py`, `-m llm`; Ollama+PG+Qdrant reales) — **arregla el pain del Slice 8**:
  1. Turno 1 (thread T): "acordate que los turnos duran 30 minutos" → chitchat responde, reflect guarda; assert fila memoria practice-scope ~"30 min".
  2. Turno 2 **en thread NUEVO T2, misma práctica** → consulta que rutea a `sql`/`rag` sobre duración → assert memoria inyectada / respuesta refleja 30 min.
  3. Assert: práctica B no recupera esa memoria.
- **Eval gate** (`app/eval/golden_set.jsonl` + `app/eval/fixtures.py::ensure_memory_fixture`): 1 caso con **siembra propia** (respeta el wipe de Qdrant de `tests/test_vectorstore.py` bajo `-m "not llm"`, igual que el fixture RAG). **PASS = aserción determinista** (el recall contiene la memoria sembrada) — no un booleano de juez suelto (señal N=1 frágil, nota Slice 11). El formato exacto del caso JSON se define en `writing-plans` leyendo `golden_set.jsonl` + el harness.

## 12. Seams para slices futuras
- **Context Manager completo:** `context.py` ya es el punto único de ensamblado → ahí entran `running_summary` (resumen incremental) + presupuesto de tokens + orden prefijo-estable/dinámico (ya respetado: estable primero, `mem_block` después).
- **Guardrails de salida `G_OUT`:** encaja **antes** de `reflect` (los terminales pasan a `G_OUT → reflect` en vez de `→ reflect`).
- **client/user-scope:** columnas ya presentes; falta "cliente en foco" en el state + PII redaction.
- **DSPy:** los prompts de gate/extract/inyección son candidatos a compilar contra el golden set.
- **Background reflection:** disparar `reflect` como task detached para bajar la latencia de cierre de turno.

## 13. Riesgos / gotchas heredados a respetar en implementación
- **Structured-output e4b `None` intermitente** → reintento ≤2x en gate/extract (patrón existente).
- **Modelo pydantic del juez sin underscore** (`GateVerdict`, no `_Gate`) — rompe el structured-output de Gemma.
- **`bge-m3` = 1024 dims** → alinear la colección `praxia_memories` con `embed_dim` o el upsert falla silenciosamente.
- **`tests/test_vectorstore.py` wipea el Qdrant compartido** bajo `-m "not llm"` → el eval-gate se auto-siembra (`ensure_memory_fixture`).
- **Windows + `dev.py`** (no uvicorn directo, ProactorEventLoop) para smoke manual.
- **`ruff format` antes de `ruff check`**; imports nuevos en tests existentes al TOP (E402); `mypy --config-file backend/pyproject.toml`.
