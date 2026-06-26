# Praxia · Fase 1 · Slice 2 — Subgrafo CRAG (RAG agéntico correctivo)

> Diseño aprobado el 2026-06-26. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slice previo (esqueleto del grafo + router): `docs/superpowers/specs/2026-06-25-grafo-router-design.md`.

## Objetivo

Reemplazar el `rag_node` **plano** de hoy (retrieve denso → synthesize → sources) por un
**subgrafo CRAG correctivo** (CLAUDE.md §4: "RAG es agéntico, no plano"):

```
retrieve (más candidatos) → rerank (bge-reranker-v2-m3) → juez de relevancia →
  (reformular + reintentar si insuficiente) → síntesis con citas (buffered) →
  juez de groundedness → emitir respuesta+citas | abstenerse
```

Beneficio colateral buscado: el juez de relevancia + la emisión de fuentes **solo en el
camino exitoso** matan de raíz el bug "la abstención muestra fuentes que no usó" (diferido a
este slice en `docs/NEXT_SESSION.md`), sin parchear el frontend.

## No-objetivos (diferidos, cada uno es trabajo propio posterior)

- **Retrieval híbrido denso+sparse** de bge-m3 (cambia el schema de la colección Qdrant a
  vectores nombrados) — Fase 1 tardío / Fase 2.
- **Prompts compilados con DSPy** (MIPROv2/GEPA) para jueces/síntesis — Fase 2. Acá los
  prompts se escriben a mano; se recompilan después contra el golden set.
- **Semantic cache** de queries repetidas y **embed cache** — Fase 2.
- **Trazas en Arize Phoenix** — Fase 2.
- **Gate formal de Ragas** (faithfulness / context precision-recall) — Fase 2. Acá se agregan
  casos al golden set y tests de comportamiento, pero la suite Ragas como gate llega en F2.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Toda inferencia por Ollama local
(`e4b` para jueces/reformulador, `12b` para síntesis). El grafo es la fuente de control: el
subgrafo CRAG se enchufa como el nodo `rag` detrás del router; no se agregan caminos que
esquiven el router ni los guardrails. Aislamiento multi-tenant por `practice_id` en todo
retrieval (CLAUDE.md §0.5).

## Arquitectura

### Decisión de límites (la que más define el diseño)

El **subgrafo es side-effect-free**: computa y devuelve `{answer, sources, grounded, abstained}`.
**Todo** el streaming SSE (`write_token` / `write_sources`) lo hace el `rag_node` externo, una
sola vez, después de la verificación. Razones:

- Encaja natural con **buffer-then-stream** (decisión de diseño): nada llega al usuario hasta
  que el juez de groundedness aprueba; la emisión ocurre toda en un punto.
- Evita propagar el stream-writer (`get_stream_writer`, contextvar) dentro de un grafo
  anidado — fuente conocida de fragilidad.
- Nodos del subgrafo testeables sin mocks de streaming; `rag_node` testea la emisión por
  separado (fuentes solo en éxito).

### Módulos

```
backend/app/
├── graph/
│   ├── rag_subgraph.py   # NUEVO: compila el StateGraph CRAG (nodos finos + edges del loop). PURO.
│   └── nodes.py          # rag_node pasa a wrapper: arma RagState, invoca el subgrafo, EMITE el SSE.
├── rag/
│   ├── rerank.py         # NUEVO: CrossEncoder bge-reranker-v2-m3 (sigmoid→score, orden, floor, top_k)
│   ├── judges.py         # NUEVO: juez de relevancia + juez de groundedness (e4b, structured output)
│   ├── reformulate.py    # NUEVO: reescritura de query (e4b, structured output)
│   ├── synthesize.py     # +synthesize() buffered (reusa el prompt/stream actual)
│   └── retrieve.py       # sin cambios de contrato (se le pide rag_fetch_k en vez de top_k)
└── llm.py                # NUEVO (chico): make_llm(model, temperature) — DRY de ChatOllama
```

- **Regla CLAUDE.md §3**: un nodo = una función pura testeable; la lógica de negocio vive en
  módulos (`rag/`), no en los nodos. Los nodos del subgrafo son finos y llaman a las funciones
  de `rag/`. Los tests las `monkeypatch` **en el namespace del módulo consumidor**
  (`rag_subgraph.rerank`, etc.) — patrón establecido en `test_nodes.py`, que parchea
  `nodes.retrieve` tras `from app.rag.retrieve import retrieve`.
- **Desvío deliberado del mapa del blueprint**: el blueprint ubica la lógica RAG en
  `agents/rag_agent.py`. Se mantiene en `rag/` porque `retrieve.py`/`synthesize.py` ya viven
  ahí; fragmentar en `agents/` ahora sería churn sin beneficio. `agents/` queda vacío hasta que
  un slice lo necesite.
- **`app/llm.py`**: factory chico que centraliza la construcción de `ChatOllama`
  (`model`, `base_url`, `temperature`). Hoy ese constructor está repetido en `router._router_llm`,
  `nodes._chitchat_llm`, `synthesize._default_llm`. Se refactorizan a usar el factory (mejora
  puntual del código que se toca; sin cambio de comportamiento).

## Flujo de datos (el loop)

```
                  ┌──────────────────── loop (rag_max_attempts = 2) ────────────────────┐
 original_query   │                                                                     │
   │              ▼                                                                      │
   ▼     retrieve(rag_fetch_k=20) → rerank(floor, top_k=5) → juez_relevancia (e4b)       │
 RagState                                                          │                     │
                                                       suficiente ─┴─ insuficiente ──┐   │
                                                           │              ┌──────────┘   │
                                                           ▼              ▼              │
                                                  synthesize (12b,   ¿quedan intentos?───┘ reformulate(e4b)
                                                  BUFFERED, citas)        │ no
                                                           │              ▼
                                                           ▼           ABSTAIN (sin fuentes)
                                                 juez_groundedness (e4b)
                                                           │
                                               grounded ───┴─── no grounded → ABSTAIN (sin fuentes)
                                                  │
                                                  ▼
                                          answer + sources → (rag_node emite por SSE)
```

Reglas del flujo:
- Se **reformula una sola vez** (`rag_max_attempts=2` → 2 retrieves como máximo). Cota dura de
  latencia en un 12b/e4b local.
- La reformulación parte siempre de la **query original** (sin drift acumulado entre intentos).
- La síntesis responde la **pregunta original del usuario**, aunque el contexto provenga del
  retrieve reformulado.
- Retrieve vacío o nada supera el floor del reranker ⇒ "insuficiente" (mismo camino:
  reformular/abstener).
- Cinturón y tiradores: si la síntesis igual devuelve `ABSTAIN_MESSAGE`, se trata como
  abstención (se saltea groundedness, sin fuentes).

## `RagState` (en `graph/rag_subgraph.py`)

TypedDict **propio del subgrafo** — mantiene `AgentState` mínimo (CLAUDE.md §4). El `rag_node`
mapea `AgentState → RagState` a la entrada y `RagState → {retrieved, sources, messages}` a la
salida.

```python
class RagState(TypedDict):
    original_query: str          # la pregunta del usuario (no muta)
    query: str                   # query de búsqueda actual (puede reformularse)
    practice_id: str
    attempts: int                # intentos de retrieve consumidos
    reranked: list[Chunk]        # top_k tras rerank+floor del intento actual
    sufficient: bool             # veredicto del juez de relevancia
    answer: str                  # borrador sintetizado (buffered)
    grounded: bool               # veredicto del juez de groundedness
    abstained: bool              # True si terminó en abstención (cualquier causa)
    sources: list[dict]          # build_sources(reranked) — solo se llena en éxito
```

El subgrafo se compila una vez a nivel módulo (`crag_app = build_crag()`), **sin checkpointer
propio**: corre como sub-invocación dentro del turno del `rag_node`; no hay HITL dentro de RAG,
así que no necesita persistencia separada.

## Config nueva (`config.py`)

| Var | Default | Para qué |
|---|---|---|
| `rerank_model` | `BAAI/bge-reranker-v2-m3` | cross-encoder local. Ya está `sentence-transformers` (lo usa bge-m3): **sin dep nueva**, sí descarga de pesos (~600MB) una vez. |
| `rag_fetch_k` | `20` | candidatos del retrieve denso antes de rerank |
| `top_k` | `5` (ya existe) | finales tras rerank |
| `rerank_min_score` | `0.2` | floor (sigmoid del logit) para descartar basura obvia. El **juez** es el gate real ⇒ floor lenient; se calibra contra el golden set. |
| `rag_max_attempts` | `2` | 1 reformulación |

Modelos: `e4b` para los dos jueces + reformulador (clasificación liviana; el router ya prueba
que `with_structured_output` anda en e4b). `12b` para síntesis. Todo local por Ollama.

## Componentes — contrato de cada uno

### `rag/rerank.py`
```python
async def rerank(query: str, chunks: list[Chunk]) -> list[Chunk]
```
- `CrossEncoder(rerank_model).predict([(query, c["text"]) for c in chunks])` → logits;
  sigmoid → score ∈ [0,1].
- Ordena desc por score, filtra `score >= rerank_min_score`, corta a `top_k`.
- `lru_cache` del modelo + `asyncio.to_thread` (igual patrón que `embeddings.py`).
- El score se usa internamente (orden + floor) y **se descarta**: `Chunk` no cambia.
- **Resiliencia**: si el cross-encoder falla en runtime → log + **fallback al orden denso**
  (primeros `top_k` de la entrada), degradado pero vivo; no rompe el turno.

### `rag/judges.py`
```python
class RelevanceVerdict(BaseModel):   sufficient: bool;  reason: str
class GroundednessVerdict(BaseModel): grounded: bool;   reason: str

async def judge_relevance(query: str, chunks: list[Chunk], llm: Any = None) -> RelevanceVerdict
async def judge_groundedness(answer: str, chunks: list[Chunk], llm: Any = None) -> GroundednessVerdict
```
- `e4b` + `with_structured_output` (decodificación restringida, CLAUDE.md §4 — prohibido regex).
- Veredicto de relevancia **binario** sobre el conjunto (no per-chunk): el reranker ya hizo el
  por-chunk; sin web search el 3-way canónico de CRAG colapsa en binario.
- LLM inyectable (`llm: Any = None`) para tests no-llm (patrón de `classify_intent`).

### `rag/reformulate.py`
```python
class Reformulation(BaseModel): query: str
async def reformulate(original_query: str, weak_chunks: list[Chunk], llm: Any = None) -> str
```
- `e4b` structured. Reescribe en español: más específico, sinónimos, términos del dominio.
  Recibe los chunks débiles como pista de qué evitar/afinar. LLM inyectable.

### `rag/synthesize.py` (+1 función)
```python
async def synthesize(query: str, chunks: list[Chunk], llm: Any = None) -> str
```
- Variante **buffered**: reusa `SYSTEM_PROMPT` y `synthesize_stream`, colecta el stream a string.
  `synthesize_stream` queda para reuso interno. La pregunta es `original_query`.

### `graph/rag_subgraph.py`
- Nodos finos: `retrieve_node`, `rerank_node`, `grade_node`, `reformulate_node`,
  `synthesize_node`, `groundedness_node` (+ helpers de abstención por bandera).
- Edges condicionales: tras `grade_node`, `sufficient` → `synthesize_node`; si no y
  `attempts < rag_max_attempts` → `reformulate_node` → vuelve a `retrieve_node`; si no →
  marca `abstained=True` → END. Tras `groundedness_node`, `grounded` → END con `sources`
  llenas; si no → `abstained=True`, `sources=[]` → END.
- `crag_app = build_crag()` a nivel módulo.

### `graph/nodes.py::rag_node` (wrapper de emisión)
```python
async def rag_node(state: AgentState) -> dict:
    result = await crag_app.ainvoke(RagState_inicial_desde(state))
    if result["abstained"]:
        write_token(ABSTAIN_MESSAGE); write_sources([])
        answer = ABSTAIN_MESSAGE; sources = []
    else:
        for piece in chunked(result["answer"]):   # replay del texto ya aprobado (efecto tipeo)
            write_token(piece)
        write_sources(result["sources"]); sources = result["sources"]; answer = result["answer"]
    return {"retrieved": result["reranked"], "sources": sources,
            "messages": [AIMessage(content=answer)]}
```
- **`write_sources` solo en el camino grounded.** Toda abstención emite `write_sources([])`.

## Manejo de errores (fail-closed)

- Ollama caído → el probe de `/chat` ya devuelve 503 antes del grafo (sin cambios en este slice).
- **Juez de relevancia** tira excepción → se trata como **insuficiente** (reformula/abstiene).
- **Juez de groundedness** tira excepción → se trata como **no grounded** → abstiene.
  (Ante duda, no se muestra nada sin verificar.)
- **Reranker** falla en runtime → fallback al orden denso, logueado.
- Retrieve vacío → abstención inmediata, sin fuentes (preserva el comportamiento actual).

## Cómo mata el bug "abstención que muestra fuentes que no usó"

De raíz, sin tocar el front: las fuentes se emiten **solo** en el camino grounded. El juez de
relevancia evita llegar a síntesis con contexto malo; el de groundedness evita emitir respuesta
no fundamentada; y **toda** rama de abstención hace `write_sources([])`. El síntoma original
(mostrar fuentes junto a una abstención) desaparece por construcción.

## Multi-tenant

`practice_id` viaja en `RagState` desde `AgentState`; `retrieve` filtra por él (CLAUDE.md §0.5).
El reranker y los jueces operan solo sobre chunks ya filtrados por práctica. Sin fugas entre
prácticas.

## Streaming / UX

- Contrato SSE del front (`event: token` / `sources` / `done`) **sin cambios**.
- Con buffer-then-stream hay un gap mayor antes del primer token (se sintetiza completo y se
  verifica antes de emitir). El `rag_node` reproduce la respuesta aprobada en chunks por
  `write_token` para conservar el efecto de tipeo.
- **(Opcional, no bloqueante de este slice)** indicador "verificando fuentes…": si el front ya
  muestra un estado de carga durante el gap, no se toca nada. Si se quiere el texto explícito,
  es un evento SSE `{"kind":"status"}` chico — nice-to-have que va con el canvas más rico
  (ítem de front diferido).

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `llm=` y `monkeypatch` de funciones de módulo
(`tests/test_router.py`, `tests/test_nodes.py`).

- **No-llm** (`tests/test_rerank.py`, `test_judges.py`, `test_reformulate.py`, `test_rag_subgraph.py`):
  - `rerank`: con un `CrossEncoder` stub (scores fijos) → orden correcto + corte por floor +
    fallback ante error del modelo.
  - `judges` / `reformulate`: construcción de prompt + parseo con LLM inyectado (fake
    `with_structured_output` como `FakeRouterLLM`).
  - **Subgrafo (el corazón)** — `crag_app` real con las funciones de `rag/` monkeypatcheadas:
    1. suficiente al primer intento → answer + sources.
    2. insuficiente → reformula → suficiente → answer.
    3. insuficiente ×2 → abstiene, sin fuentes.
    4. grounded → answer + sources.
    5. no grounded → abstiene, sin fuentes.
  - `rag_node`: **fuentes solo en éxito**; `write_sources([])` en toda abstención (reusa el
    helper `_run` de `test_nodes.py` con `stream_mode="custom"`).
- **`-m llm`** (`tests/test_e2e_llm.py`, Ollama+Qdrant reales, doc semilla): query relevante →
  respuesta citada y grounded; query off-topic → abstención sin fuentes.
- **Golden set**: agregar a `eval/golden_set.jsonl` casos de "abstención sin fuentes" y
  "respuesta con citas" (el gate Ragas formal es F2; los casos quedan listos).
- **Gates**: `ruff` + `mypy app/` + `pytest -q` verdes; smoke de CLAUDE.md §2 sigue pasando;
  las escrituras siguen sin existir / pidiendo confirmación (no se tocan los stubs).

## Dependencias

Ninguna nueva: `sentence-transformers` (ya presente para bge-m3) provee `CrossEncoder`. Se
descargan los pesos de `bge-reranker-v2-m3` una vez (red permitida solo para bajar modelos,
CLAUDE.md §0). Sin servicios cloud, sin APIs pagas, sin red saliente fuera de
Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff check . && ruff format .`, `mypy app/` y `pytest -q` (no-llm) verdes; `-m llm` verde
   con Ollama + ambos modelos + infra.
2. Tocamos retrieval/síntesis: la suite offline no regresiona; se agregan casos al golden set.
3. Tocamos grafo: el smoke de §2 pasa (chitchat / RAG con citas / stub) y las **escrituras
   siguen pidiendo confirmación** (stubs intactos).
4. Prompts de alto apalancamiento (jueces, síntesis, reformulador) escritos a mano ahora;
   recompilar con DSPy queda anotado para Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Latencia del pipeline en local** (retrieve + rerank + ≥1 juez + síntesis + juez): mitigada
  por jueces/reformulador en `e4b`, cap de 1 reformulación, y floor lenient para no
  sobre-reformular. Si pesa, el siguiente paso natural es semantic cache (F2).
- **`bge-reranker-v2-m3` vía `CrossEncoder`**: devuelve logits, no probabilidades → aplicar
  sigmoid antes del floor. Cubierto por `test_rerank.py`.
- **Carga de pesos del reranker** (~600MB, primera vez): descarga única; el fallback al orden
  denso evita que un fallo de carga tumbe el turno.
- **Fragilidad del e4b en salida estructurada** (CLAUDE.md §9): decodificación restringida (no
  regex); casos límite van al golden set y se ajusta el prompt (DSPy en F2).
- **Streaming a través del subgrafo**: evitado por diseño — el subgrafo es puro y el `rag_node`
  emite; no se propaga el stream-writer dentro del grafo anidado.
