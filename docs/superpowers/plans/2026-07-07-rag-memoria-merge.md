# RAG memory-aware (paralelo + merge + precedencia de memoria) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el subgrafo CRAG responda combinando documentos y memoria (con precedencia de memoria en conflicto) en vez de abstenerse cuando el dato vive en la memoria, sin regresionar el camino doc-only.

**Architecture:** Se teje la memoria (ya presente en `RagState.memories`) en los tres puntos hoy doc-only de CRAG — juez de relevancia (`grade`), síntesis y juez de groundedness — con un **invariante de branching**: si `memories == []`, los prompts/inputs son byte-idénticos a los actuales (protege el eval gate por construcción). Una sola síntesis produce la respuesta combinada; las fuentes mostradas se filtran a los chunks realmente citados cuando hay memoria. Un kill switch (`rag_memory_merge_enabled`) revierte RAG a doc-only sin tocar la memoria de chitchat/SQL.

**Tech Stack:** Python 3.11+ async, LangGraph (subgrafo `crag_app`), Ollama local (`gemma4:12b` síntesis, `gemma4:e4b` jueces), Qdrant/Postgres, pytest (markers `llm`/`eval`), ruff, mypy.

## Global Constraints

- **Local-first / $0:** toda inferencia por Ollama en `http://localhost:11434`. Cero red saliente nueva desde el código de Praxia (solo Ollama/Qdrant/Postgres locales).
- **Multi-tenant:** `recall` ya filtra por `practice_id`+`scope='practice'`; no se afloja. La síntesis/jueces solo ven memorias de la práctica del turno.
- **Invariante de no-regresión:** con `memories == []`, jueces y síntesis usan los strings/inputs actuales **literales**. El eval gate y el gate `-m "not llm"` no deben regresionar.
- **No tocar el HITL** de escrituras CRM (`propose_action`/`confirm_action`) ni el frontend. Cero DDL. Cero deps nuevas.
- **Commits LIMPIOS:** el autor es el usuario; **prohibido** cualquier trailer/atribución `Co-Authored-By: Claude` o mención al asistente (CLAUDE.md §6, innegociable).
- **Loop de calidad (Windows):** `cd backend`; usar el venv del repo `backend\.venv\Scripts\python`. `ruff format .` **antes** de `ruff check .`; luego `mypy app/`; luego `pytest`. Imports nuevos en tests existentes al TOP (ruff E402). No meter literales int ≥ 2^64 en `app/` (envenena la cache de mypy pineado `1.13.*`).
- **Suite completa tras cambios de firma cross-cutting:** `judge_relevance`/`judge_groundedness`/`synthesize` cambian de firma (param `memories` opcional). Correr `pytest backend/tests -m "not llm"` COMPLETO, no solo los archivos tocados (lección Slice 12).
- **Docker arriba** (postgres+qdrant) para `-m "not llm"`. Ollama arriba para `-m llm` y el eval gate.

---

## File Structure

**Modificados (superficie chica):**
- `backend/app/rag/synthesize.py` — helper `memories_text`, prompt de síntesis con memoria+precedencia (branch), fix del guard `if not chunks and not memories`, nuevo `select_sources`. Elimina el uso (inerte) de `format_memories_block`.
- `backend/app/rag/judges.py` — `judge_relevance`/`judge_groundedness` memory-aware (branch).
- `backend/app/graph/rag_subgraph.py` — `grade_node`/`groundedness_node` pasan `state["memories"]`; `groundedness_node` usa `select_sources`.
- `backend/app/graph/nodes.py` — `rag_node` respeta el kill switch.
- `backend/app/config.py` — `rag_memory_merge_enabled`.
- `backend/app/eval/{cases,checks,run}.py` + `golden_set.jsonl` — behavior `memory_answer` + seed/forget por caso.

**Tests modificados/creados:**
- `test_synthesize.py`, `test_judges.py`, `test_rag_subgraph.py`, `test_memory_injection.py`, `test_nodes.py`, `test_config.py`, `test_eval_cases.py`, `test_eval_checks.py`.
- **Nuevo** `test_rag_memory_e2e_llm.py` (marker `llm`).

---

## Task 1: Síntesis memory-aware con precedencia (`synthesize.py`)

**Files:**
- Modify: `backend/app/rag/synthesize.py` (imports, líneas 8-15 prompt, 60-85 stream/buffered)
- Test: `backend/tests/test_synthesize.py`
- Test (fix): `backend/tests/test_memory_injection.py:84-114`

**Interfaces:**
- Consumes: `Chunk` (TypedDict), `ABSTAIN_MESSAGE`, `_format_context`, `_default_llm` (ya existen en el módulo).
- Produces:
  - `memories_text(memories: list[dict]) -> str` — bullets `- {content}`.
  - `SYSTEM_PROMPT_WITH_MEMORY: str`.
  - `synthesize_stream(query, chunks, llm=None, memories=None)` y `synthesize(query, chunks, llm=None, memories=None)` — ahora usan la rama con memoria cuando `memories` no está vacío; guard abstiene solo si `not chunks and not memories`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_synthesize.py`:

```python
async def test_memory_only_uses_memory_branch_and_does_not_abstain_guard():
    """Sin chunks pero CON memoria: NO abstiene por el guard; usa la rama con memoria."""

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    captured = {}

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("La seña es de $5000, según me indicaste.")

    out = await synthesize.synthesize(
        "¿cuánto vale la seña?",
        [],
        llm=FakeLLM(),
        memories=[{"content": "La seña vale $5000.", "kind": "hecho"}],
    )
    assert out == "La seña es de $5000, según me indicaste."
    # la memoria va en el mensaje human (no como system)
    human_texts = [m[1] for m in captured["messages"] if m[0] == "human"]
    assert any("La seña vale $5000." in t for t in human_texts)
    system_texts = [m[1] for m in captured["messages"] if m[0] == "system"]
    assert system_texts and system_texts[0] == synthesize.SYSTEM_PROMPT_WITH_MEMORY


async def test_no_memory_branch_is_byte_identical_system_and_human():
    """Invariante: sin memoria, system == SYSTEM_PROMPT y human sin sección de memoria."""

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    captured = {}

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("Según el protocolo [1].")

    await synthesize.synthesize("¿cuánto dura?", [_chunk()], llm=FakeLLM())
    assert captured["messages"][0] == ("system", synthesize.SYSTEM_PROMPT)
    human = [m[1] for m in captured["messages"] if m[0] == "human"][0]
    assert human == f"Fragmentos:\n\n{synthesize._format_context([_chunk()])}\n\nPregunta: ¿cuánto dura?"
    assert "memoria" not in human.lower()


def test_memories_text_formats_bullets():
    out = synthesize.memories_text([{"content": "A."}, {"content": "B."}])
    assert out == "- A.\n- B."


async def test_abstains_when_no_chunks_and_no_memories():
    out = await synthesize.synthesize("hola", [], memories=[])
    assert out == synthesize.ABSTAIN_MESSAGE
```

Y **arreglar** el test que se rompe en `backend/tests/test_memory_injection.py:84-114` (`test_rag_synthesis_injects_memories`): la memoria ahora va en `human`, no en `system`. Reemplazar las últimas dos líneas del test:

```python
    # ANTES:
    # system_texts = [m[1] for m in captured["messages"] if m[0] == "system"]
    # assert any("pacientes" in t for t in system_texts)
    # DESPUÉS:
    human_texts = [m[1] for m in captured["messages"] if m[0] == "human"]
    assert any("pacientes" in t for t in human_texts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_synthesize.py tests/test_memory_injection.py::test_rag_synthesis_injects_memories -q`
Expected: FAIL (`AttributeError: module 'app.rag.synthesize' has no attribute 'memories_text' / 'SYSTEM_PROMPT_WITH_MEMORY'`; el test de injection falla por `system` vs `human`).

- [ ] **Step 3: Implement in `synthesize.py`**

Quitar el import inerte (línea ~5) `from app.context import format_memories_block`. Agregar `memories_text`, el prompt con memoria, y reescribir `synthesize_stream`:

```python
# (arriba, junto a ABSTAIN_MESSAGE / SYSTEM_PROMPT)
SYSTEM_PROMPT_WITH_MEMORY = (
    "Sos el asistente de una práctica profesional. Respondé en español usando ÚNICAMENTE las "
    "fuentes provistas: los FRAGMENTOS de documentos y lo que el usuario te indicó (memoria).\n"
    "- Citá cada fragmento que uses con la marca [n].\n"
    "- Lo que te indicó el usuario NO lleva [n]; cuando lo uses, atribuílo en el texto "
    "(por ejemplo: 'según me indicaste').\n"
    "- Si algo que te indicó el usuario CONTRADICE un fragmento sobre el mismo dato, priorizá "
    "lo que te indicó el usuario (es lo más reciente) y aclará la diferencia (por ejemplo: "
    "'el protocolo indica 45 minutos, aunque me señalaste que ahora son 60').\n"
    "- Usá lo que te indicó el usuario SOLO si responde la pregunta; ignorá lo que no aplique.\n"
    "- Si NI los fragmentos NI lo que te indicó el usuario contienen la respuesta, respondé "
    f"exactamente: '{ABSTAIN_MESSAGE}'.\n"
    "No inventes ni uses conocimiento externo."
)


def memories_text(memories: list[dict]) -> str:
    """Formatea memorias como lista para el bloque de evidencia (síntesis/jueces)."""
    return "\n".join(f"- {m['content']}" for m in memories)
```

Reescribir `synthesize_stream` (reemplaza el cuerpo actual, líneas ~60-75):

```python
async def synthesize_stream(
    query: str, chunks: list[Chunk], llm: Any = None, memories: list[dict] | None = None
) -> AsyncIterator[str]:
    memories = memories or []
    if not chunks and not memories:
        yield ABSTAIN_MESSAGE
        return
    llm = llm or _default_llm()
    if memories:
        system = SYSTEM_PROMPT_WITH_MEMORY
        human = (
            f"Fragmentos:\n\n{_format_context(chunks)}\n\n"
            f"Lo que me indicaste (memoria):\n{memories_text(memories)}\n\n"
            f"Pregunta: {query}"
        )
    else:
        system = SYSTEM_PROMPT
        human = f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"
    messages: list[tuple[str, str]] = [("system", system), ("human", human)]
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            yield text
```

`synthesize` (buffered) NO cambia: ya delega en `synthesize_stream(query, chunks, llm=llm, memories=memories)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_synthesize.py tests/test_memory_injection.py -q`
Expected: PASS (todos, incluidos los `test_abstains_without_context`/`test_streams_and_cites_with_context` preexistentes por el invariante).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/synthesize.py backend/tests/test_synthesize.py backend/tests/test_memory_injection.py
git commit -m "feat(rag-memoria): sintesis memory-aware con precedencia (branch invariante sin memoria)"
```

---

## Task 2: `select_sources` — fuentes cited-only con memoria (`synthesize.py`)

**Files:**
- Modify: `backend/app/rag/synthesize.py` (junto a `build_sources`)
- Test: `backend/tests/test_synthesize.py`

**Interfaces:**
- Consumes: `build_sources(chunks) -> list[dict]` (ya existe).
- Produces: `select_sources(chunks: list[Chunk], answer: str, memories: list[dict]) -> list[dict]` — sin memorias devuelve `build_sources(chunks)` (histórico); con memorias devuelve solo las fuentes cuyo `[n]` aparece en `answer`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_synthesize.py`:

```python
def test_select_sources_no_memories_returns_all():
    chunks = [_chunk()]
    assert synthesize.select_sources(chunks, "cualquier cosa", []) == synthesize.build_sources(chunks)


def test_select_sources_memory_only_answer_returns_empty():
    chunks = [_chunk()]
    # answer sin marcas [n] (respuesta desde memoria) ⇒ sin fuentes
    assert synthesize.select_sources(chunks, "Según me indicaste, dura 90 min.", [{"content": "x"}]) == []


def test_select_sources_merge_returns_only_cited():
    c1 = _chunk()
    c2 = Chunk(text="otro", page=None, chunk_index=1, document_id="doc-2", title="Otro", doc_type="x")
    out = synthesize.select_sources([c1, c2], "Dura 60 [1]. Además me indicaste algo.", [{"content": "x"}])
    assert out == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_synthesize.py -k select_sources -q`
Expected: FAIL (`AttributeError: ... has no attribute 'select_sources'`).

- [ ] **Step 3: Implement `select_sources` in `synthesize.py`** (debajo de `build_sources`)

```python
def select_sources(chunks: list[Chunk], answer: str, memories: list[dict]) -> list[dict[str, Any]]:
    """Fuentes a mostrar. Sin memorias: todas las reranked (comportamiento histórico).
    Con memorias: solo los chunks efectivamente citados [n] en el answer (memory-only ⇒ [])."""
    all_sources = build_sources(chunks)
    if not memories:
        return all_sources
    return [s for s in all_sources if f"[{s['n']}]" in answer]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_synthesize.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/synthesize.py backend/tests/test_synthesize.py
git commit -m "feat(rag-memoria): select_sources (cited-only con memoria; doc-only intacto)"
```

---

## Task 3: Jueces memory-aware (`judges.py`)

**Files:**
- Modify: `backend/app/rag/judges.py` (imports línea 7, prompts 9-23, funciones 40-59)
- Test: `backend/tests/test_judges.py`

**Interfaces:**
- Consumes: `memories_text` (Task 1), `chunks_text` (ya importado), `RelevanceVerdict`, `GroundednessVerdict`.
- Produces:
  - `judge_relevance(query, chunks, memories=None, llm=None) -> RelevanceVerdict`
  - `judge_groundedness(answer, chunks, memories=None, llm=None) -> GroundednessVerdict`
  - Constantes `RELEVANCE_PROMPT_WITH_MEMORY`, `GROUNDEDNESS_PROMPT_WITH_MEMORY`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_judges.py` (el `FakeLLM`/`FakeStructured` ya existen y capturan; extenderlos para capturar mensajes):

```python
class CapturingStructured:
    def __init__(self, value, sink):
        self._value = value
        self._sink = sink

    async def ainvoke(self, messages):
        self._sink["messages"] = messages
        return self._value


class CapturingLLM:
    def __init__(self, value, sink):
        self._value = value
        self._sink = sink

    def with_structured_output(self, schema):
        return CapturingStructured(self._value, self._sink)


async def test_relevance_with_memories_adds_memory_section_and_prompt():
    sink = {}
    llm = CapturingLLM(judges.RelevanceVerdict(sufficient=True, reason="ok"), sink)
    await judges.judge_relevance("q", [_c()], memories=[{"content": "dato de memoria"}], llm=llm)
    assert sink["messages"][0] == ("system", judges.RELEVANCE_PROMPT_WITH_MEMORY)
    human = sink["messages"][1][1]
    assert "dato de memoria" in human


async def test_relevance_without_memories_is_identical_to_today():
    sink = {}
    llm = CapturingLLM(judges.RelevanceVerdict(sufficient=True, reason="ok"), sink)
    await judges.judge_relevance("q", [_c()], llm=llm)
    assert sink["messages"][0] == ("system", judges.RELEVANCE_PROMPT)
    assert sink["messages"][1] == ("human", f"Pregunta: q\n\nFragmentos:\n{judges.chunks_text([_c()])}")


async def test_groundedness_with_memories_uses_memory_prompt():
    sink = {}
    llm = CapturingLLM(judges.GroundednessVerdict(grounded=True, reason="ok"), sink)
    await judges.judge_groundedness("ans", [_c()], memories=[{"content": "m"}], llm=llm)
    assert sink["messages"][0] == ("system", judges.GROUNDEDNESS_PROMPT_WITH_MEMORY)
    assert "m" in sink["messages"][1][1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_judges.py -q`
Expected: FAIL (`AttributeError: ... RELEVANCE_PROMPT_WITH_MEMORY`).

- [ ] **Step 3: Implement in `judges.py`**

Import (línea 7): `from app.rag.synthesize import chunks_text, memories_text`.

Agregar prompts (debajo de los actuales):

```python
RELEVANCE_PROMPT_WITH_MEMORY = (
    "Sos un evaluador de relevancia de un CRM para prácticas profesionales. Dada una pregunta, "
    "fragmentos de documentos y hechos que el usuario le indicó a Praxia (memoria), decidí si la "
    "COMBINACIÓN contiene información SUFICIENTE para responder. Respondé sufficient=true si la "
    "respuesta puede fundamentarse en los fragmentos O en la memoria; si ni los fragmentos ni la "
    "memoria tienen el dato, sufficient=false. Incluí una razón breve en español."
)

GROUNDEDNESS_PROMPT_WITH_MEMORY = (
    "Sos un verificador de fundamentación de un CRM para prácticas profesionales. Dada una "
    "respuesta, los fragmentos fuente y los hechos que el usuario indicó (memoria), decidí si CADA "
    "afirmación está respaldada por los fragmentos O por la memoria. grounded=true solo si todo lo "
    "afirmado se verifica en los fragmentos o en la memoria; si hay datos inventados o no "
    "presentes en ninguna fuente, grounded=false. Incluí una razón breve en español."
)
```

Reescribir las dos funciones (líneas ~40-59):

```python
async def judge_relevance(
    query: str, chunks: list[Chunk], memories: list[dict] | None = None, llm: Any = None
) -> RelevanceVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(RelevanceVerdict)
    memories = memories or []
    if memories:
        system = RELEVANCE_PROMPT_WITH_MEMORY
        human = f"Pregunta: {query}\n\nFragmentos:\n{chunks_text(chunks)}\n\nMemoria:\n{memories_text(memories)}"
    else:
        system = RELEVANCE_PROMPT
        human = f"Pregunta: {query}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: RelevanceVerdict = await structured.ainvoke([("system", system), ("human", human)])
    return verdict


async def judge_groundedness(
    answer: str, chunks: list[Chunk], memories: list[dict] | None = None, llm: Any = None
) -> GroundednessVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(GroundednessVerdict)
    memories = memories or []
    if memories:
        system = GROUNDEDNESS_PROMPT_WITH_MEMORY
        human = f"Respuesta:\n{answer}\n\nFragmentos:\n{chunks_text(chunks)}\n\nMemoria:\n{memories_text(memories)}"
    else:
        system = GROUNDEDNESS_PROMPT
        human = f"Respuesta:\n{answer}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: GroundednessVerdict = await structured.ainvoke([("system", system), ("human", human)])
    return verdict
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_judges.py -q`
Expected: PASS (los 4 tests preexistentes que llaman `judge_relevance("q", [_c()], llm=llm)` siguen verdes: `memories` default `None`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/judges.py backend/tests/test_judges.py
git commit -m "feat(rag-memoria): jueces relevancia/groundedness memory-aware (branch invariante)"
```

---

## Task 4: Wiring en el subgrafo CRAG (`rag_subgraph.py`)

**Files:**
- Modify: `backend/app/graph/rag_subgraph.py` (import línea 12, `grade_node` 56-64, `groundedness_node` 94-107)
- Test: `backend/tests/test_rag_subgraph.py` (actualizar fakes + tests nuevos)

**Interfaces:**
- Consumes: `judge_relevance(query, chunks, memories=…)` (Task 3), `judge_groundedness(answer, chunks, memories=…)` (Task 3), `select_sources(chunks, answer, memories)` (Task 2).
- Produces: subgrafo `crag_app` que NO abstiene cuando la memoria responde y filtra fuentes a las citadas.

- [ ] **Step 1: Write the failing tests**

En `backend/tests/test_rag_subgraph.py`: (a) **actualizar las firmas de TODOS los fakes** `jr`/`jg` para aceptar `memories=None` (hoy son `async def jr(q, chunks, llm=None)` y `async def jg(a, chunks, llm=None)` → pasar a `async def jr(q, chunks, memories=None, llm=None)` y `async def jg(a, chunks, memories=None, llm=None)`), y (b) agregar tests nuevos:

```python
async def test_memory_relevant_with_offtopic_docs_does_not_abstain(monkeypatch):
    """El bug: docs off-topic + memoria relevante ⇒ NO abstiene, responde con el hecho."""

    async def jr(q, chunks, memories=None, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="memoria responde")

    async def synth(q, chunks, **kwargs):
        assert kwargs.get("memories"), "la memoria debe llegar a la síntesis"
        return "La seña vale $5000, según me indicaste."

    async def jg(a, chunks, memories=None, llm=None):
        assert memories, "la memoria debe llegar al juez de groundedness"
        return judges.GroundednessVerdict(grounded=True, reason="respaldado por memoria")

    _patch(monkeypatch, retrieve=_ok_retrieve, rerank=_ok_rerank,
           judge_relevance=jr, synthesize=synth, judge_groundedness=jg)
    state = rag_subgraph.initial_rag_state(
        "¿cuánto vale la seña?", "p", memories=[{"content": "La seña vale $5000."}])
    out = await rag_subgraph.crag_app.ainvoke(state)
    assert out["abstained"] is False
    assert "$5000" in out["answer"]
    assert out["sources"] == []  # answer sin [n] ⇒ memory-only, sin fuentes


async def test_memory_only_empty_rerank_still_grades_with_memory(monkeypatch):
    """reranked vacío pero con memoria ⇒ grade NO corta; consulta al juez memory-aware."""
    calls = {"jr": 0}

    async def empty_rerank(query, chunks):
        return []

    async def jr(q, chunks, memories=None, llm=None):
        calls["jr"] += 1
        return judges.RelevanceVerdict(sufficient=True, reason="memoria")

    async def synth(q, chunks, **kwargs):
        return "Respuesta desde memoria."

    async def jg(a, chunks, memories=None, llm=None):
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    _patch(monkeypatch, retrieve=_ok_retrieve, rerank=empty_rerank,
           judge_relevance=jr, synthesize=synth, judge_groundedness=jg)
    state = rag_subgraph.initial_rag_state("q", "p", memories=[{"content": "algo"}])
    out = await rag_subgraph.crag_app.ainvoke(state)
    assert calls["jr"] == 1
    assert out["abstained"] is False


async def test_merge_answer_keeps_only_cited_sources(monkeypatch):
    """Merge: answer cita [1] y usa memoria ⇒ sources = solo el chunk citado."""

    async def jr(q, chunks, memories=None, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def synth(q, chunks, **kwargs):
        return "Dura 60 min [1], aunque me indicaste que ahora son 90."

    async def jg(a, chunks, memories=None, llm=None):
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    _patch(monkeypatch, retrieve=_ok_retrieve, rerank=_ok_rerank,
           judge_relevance=jr, synthesize=synth, judge_groundedness=jg)
    state = rag_subgraph.initial_rag_state("¿cuánto dura?", "p", memories=[{"content": "90 min"}])
    out = await rag_subgraph.crag_app.ainvoke(state)
    assert out["sources"] == [{"n": 1, "title": "Protocolo", "page": None, "document_id": "1"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_rag_subgraph.py -q`
Expected: FAIL — los tests nuevos fallan porque `grade_node` corta con `reranked` vacío / `groundedness_node` no pasa `memories` ni filtra fuentes. (Los preexistentes que aún tengan fakes viejos también fallarían con `TypeError` una vez implementado el paso 3 — por eso el paso 1 ya actualizó sus firmas.)

- [ ] **Step 3: Implement in `rag_subgraph.py`**

Import (línea 12): `from app.rag.synthesize import ABSTAIN_MESSAGE, select_sources, synthesize` (reemplaza `build_sources` por `select_sources`).

`grade_node` (líneas ~56-64):

```python
async def grade_node(state: RagState) -> dict[str, Any]:
    memories = state.get("memories", [])
    if not state["reranked"] and not memories:
        return {"sufficient": False}
    try:
        verdict = await judge_relevance(
            state["original_query"], state["reranked"], memories=memories
        )
        return {"sufficient": verdict.sufficient}
    except Exception:
        logger.warning("juez de relevancia falló; trato como insuficiente", exc_info=True)
        return {"sufficient": False}
```

`groundedness_node` (líneas ~94-107):

```python
async def groundedness_node(state: RagState) -> dict[str, Any]:
    memories = state.get("memories", [])
    try:
        verdict = await judge_groundedness(state["answer"], state["reranked"], memories=memories)
        grounded = verdict.grounded
    except Exception:
        logger.warning("juez de groundedness falló; trato como no fundamentado", exc_info=True)
        grounded = False
    if grounded:
        return {
            "grounded": True,
            "abstained": False,
            "sources": select_sources(state["reranked"], state["answer"], memories),
        }
    return {"grounded": False}
```

`synthesize_node` NO cambia (ya pasa `memories=state.get("memories", [])`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_rag_subgraph.py -q`
Expected: PASS (nuevos + preexistentes; `test_empty_rerank_is_insufficient_without_calling_judge` sigue verde porque usa `initial_rag_state("q","p")` ⇒ `memories=[]` ⇒ corta sin juez).

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph/rag_subgraph.py backend/tests/test_rag_subgraph.py
git commit -m "feat(rag-memoria): CRAG teje memoria en grade/groundedness + fuentes cited-only"
```

---

## Task 5: Kill switch `rag_memory_merge_enabled` (`config.py` + `rag_node`)

**Files:**
- Modify: `backend/app/config.py` (después de la sección Memoria RICA, ~línea 53)
- Modify: `backend/app/graph/nodes.py::rag_node` (líneas 57-79)
- Test: `backend/tests/test_config.py`, `backend/tests/test_nodes.py`

**Interfaces:**
- Consumes: `get_settings()` (ya importado en `nodes.py`).
- Produces: `Settings.rag_memory_merge_enabled: bool = True`; `rag_node` pasa `memories=[]` al subgrafo cuando el flag es `False`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_config.py`:

```python
def test_rag_memory_merge_enabled_default_true():
    from app.config import Settings

    assert Settings().rag_memory_merge_enabled is True
```

Agregar a `backend/tests/test_nodes.py`. `rag_node` usa `get_stream_writer()` (vía `write_token`/`write_sources`) → **debe** correr dentro de un grafo de un nodo con `stream_mode="custom"`. El helper `_one_node_graph` se define local en el test (patrón de `test_memory_injection.py:7-18`); si `test_nodes.py` ya lo tiene, reusarlo:

```python
from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import AgentState, new_state


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run_capturing_crag(monkeypatch, *, flag: bool, memories: list[dict]) -> dict:
    from types import SimpleNamespace

    captured: dict = {}

    class FakeCrag:
        async def ainvoke(self, state):
            captured["memories"] = state["memories"]
            return {"answer": "x", "abstained": True, "reranked": [], "sources": []}

    monkeypatch.setattr(nodes, "crag_app", FakeCrag())
    monkeypatch.setattr(nodes, "get_settings", lambda: SimpleNamespace(rag_memory_merge_enabled=flag))
    state = new_state("¿cuánto vale la seña?", "p", "t")
    state["memories"] = memories
    async for _ in _one_node_graph(nodes.rag_node).astream(state, stream_mode="custom"):
        pass
    return captured


async def test_rag_node_kill_switch_zeroes_memories(monkeypatch):
    captured = await _run_capturing_crag(
        monkeypatch, flag=False, memories=[{"content": "La seña vale $5000."}]
    )
    assert captured["memories"] == []


async def test_rag_node_passes_memories_when_enabled(monkeypatch):
    captured = await _run_capturing_crag(
        monkeypatch, flag=True, memories=[{"content": "algo"}]
    )
    assert captured["memories"] == [{"content": "algo"}]
```

> Nota E402: si estos son los primeros imports que agregás a `test_nodes.py`, ponelos al TOP del archivo (ruff E402), no entre funciones.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_config.py::test_rag_memory_merge_enabled_default_true tests/test_nodes.py -k "kill_switch or passes_memories" -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'rag_memory_merge_enabled'`).

- [ ] **Step 3: Implement**

`config.py` (después de la sección "Memoria RICA (Fase 2 Slice 4)", ~línea 53):

```python
    # RAG memory-aware (Fase 2 fast-follow #1)
    rag_memory_merge_enabled: bool = True  # False ⇒ RAG doc-only (no toca la memoria de chitchat/SQL)
```

`nodes.py::rag_node` (reemplazar líneas 57-62 — la construcción del estado inicial):

```python
async def rag_node(state: AgentState) -> dict:
    memories = state.get("memories", []) if get_settings().rag_memory_merge_enabled else []
    result = await crag_app.ainvoke(
        initial_rag_state(last_user_text(state), state["practice_id"], memories=memories)
    )
    answer = result["answer"]
    # ... (resto del cuerpo idéntico)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_config.py tests/test_nodes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/graph/nodes.py backend/tests/test_config.py backend/tests/test_nodes.py
git commit -m "feat(rag-memoria): kill switch rag_memory_merge_enabled (revierte RAG a doc-only)"
```

---

## Task 6: Gate completo `-m "not llm"` + e2e-llm del bug (`test_rag_memory_e2e_llm.py`)

**Files:**
- Create: `backend/tests/test_rag_memory_e2e_llm.py` (marker `llm`)

**Interfaces:**
- Consumes: `rag_subgraph.crag_app`, `rag_subgraph.initial_rag_state`, `rag_subgraph.retrieve`/`rerank` (monkeypatch de evidencia), Ollama real (jueces `e4b` + síntesis `12b`).
- Produces: prueba de regresión del bug (memory-only no abstiene) y de la precedencia (memoria gana al doc).

- [ ] **Step 1: Correr el gate completo `-m "not llm"` (regresión cross-cutting)**

Run: `cd backend && .venv\Scripts\python -m pytest tests -m "not llm" -q`
Expected: PASS — **378 + los nuevos** (docker arriba). Si algún test que usa `judge_relevance`/`judge_groundedness`/`synthesize` falla por firma, arreglar su fake acá (no debería: los params son opcionales).

- [ ] **Step 2: Write the e2e-llm tests (fallan sin Ollama; se corren aparte)**

Crear `backend/tests/test_rag_memory_e2e_llm.py`:

```python
import pytest

from app.graph import rag_subgraph
from app.models import Chunk

pytestmark = pytest.mark.llm


def _chunk(text: str) -> Chunk:
    return Chunk(
        text=text, page=1, chunk_index=0, document_id="d1", title="Protocolo", doc_type="protocolo"
    )


async def test_memory_only_does_not_abstain(monkeypatch):
    """Regresión del bug: docs vacíos + memoria relevante ⇒ NO abstiene (jueces+síntesis reales)."""

    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    async def fake_rerank(query, chunks):
        return chunks

    monkeypatch.setattr(rag_subgraph, "retrieve", fake_retrieve)
    monkeypatch.setattr(rag_subgraph, "rerank", fake_rerank)
    memories = [{"content": "La seña para reservar un turno es de 5000 pesos.", "kind": "hecho"}]
    out = await rag_subgraph.crag_app.ainvoke(
        rag_subgraph.initial_rag_state("¿cuánto hay que dejar de seña?", "p", memories=memories)
    )
    assert out["abstained"] is False, f"no debió abstenerse; answer={out['answer']!r}"
    assert "5000" in out["answer"]
    assert out["sources"] == []  # memory-only ⇒ sin tarjeta de fuentes


async def test_merge_precedence_memory_over_doc(monkeypatch):
    """Precedencia: doc dice 60, memoria dice 90 ⇒ la respuesta lidera con 90 (memoria)."""

    async def fake_retrieve(query, practice_id=None, top_k=None):
        return [_chunk("La primera consulta dura 60 minutos.")]

    async def fake_rerank(query, chunks):
        return chunks

    monkeypatch.setattr(rag_subgraph, "retrieve", fake_retrieve)
    monkeypatch.setattr(rag_subgraph, "rerank", fake_rerank)
    memories = [{"content": "La primera consulta ahora dura 90 minutos.", "kind": "hecho"}]
    out = await rag_subgraph.crag_app.ainvoke(
        rag_subgraph.initial_rag_state("¿cuánto dura la primera consulta?", "p", memories=memories)
    )
    assert out["abstained"] is False, f"answer={out['answer']!r}"
    assert "90" in out["answer"], f"precedencia de memoria; answer={out['answer']!r}"
```

- [ ] **Step 3: Run the e2e-llm tests (Ollama arriba)**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_rag_memory_e2e_llm.py -q`
Expected: PASS. (Si `test_merge_precedence` flaquea por variance del 12b, mantener la aserción dura en `"90"` presente — es el mecanismo; NO asertar el wording exacto de la aclaración de precedencia.)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_rag_memory_e2e_llm.py
git commit -m "test(rag-memoria): e2e-llm memory-only (regresion del bug) + precedencia memoria>doc"
```

---

## Task 7: Caso de memoria en el eval-gate (cierra "Task 11")

**Files:**
- Modify: `backend/app/eval/cases.py` (`_BEHAVIORS` 9, `EvalCase` 15-24, `load_golden_set` 63-72, `_validate` 37-52)
- Modify: `backend/app/eval/checks.py` (`deterministic_failures` 14-28)
- Modify: `backend/app/eval/run.py` (`_score_case` 37-51)
- Modify: `backend/app/eval/golden_set.jsonl` (línea nueva)
- Test: `backend/tests/test_eval_cases.py`, `backend/tests/test_eval_checks.py`

**Interfaces:**
- Consumes: `long_term.store`/`forget`/`ensure_memories_collection` (Slice 12/14), `get_settings().practice_id`.
- Produces: `expected_behavior="memory_answer"` (determinista puro, sin `RagSample` ⇒ no perturba el baseline); `EvalCase.seed_memory`; seed+forget por caso en `_score_case`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_eval_cases.py`:

```python
def test_memory_answer_case_validates():
    from app.eval.cases import EvalCase, _validate

    case = EvalCase(
        question="¿hay que dejar seña?",
        category="rag",
        intent="rag",
        expected_behavior="memory_answer",
        must_include=["5000"],
        seed_memory="Para reservar hay que dejar una seña de 5000 pesos.",
    )
    _validate(case)  # no raise


def test_memory_answer_requires_seed_memory():
    import pytest

    from app.eval.cases import EvalCase, _validate

    case = EvalCase(
        question="q", category="rag", intent="rag",
        expected_behavior="memory_answer", must_include=["x"], seed_memory=None,
    )
    with pytest.raises(ValueError):
        _validate(case)
```

Agregar a `backend/tests/test_eval_checks.py`:

```python
def test_memory_answer_with_sources_fails():
    from app.eval.cases import CaseResult, EvalCase
    from app.eval.checks import deterministic_failures

    case = EvalCase(
        question="q", category="rag", intent="rag",
        expected_behavior="memory_answer", must_include=["5000"],
        seed_memory="La seña es 5000.",
    )
    result = CaseResult(
        case=case, intent="rag", answer="La seña es 5000, según me indicaste.",
        retrieved=[], sources=[{"n": 1, "title": "x", "page": None, "document_id": "d"}],
        candidate_sql="",
    )
    assert any("memory_answer con sources" in f for f in deterministic_failures(result))


def test_memory_answer_without_sources_passes_source_check():
    from app.eval.cases import CaseResult, EvalCase
    from app.eval.checks import deterministic_failures

    case = EvalCase(
        question="q", category="rag", intent="rag",
        expected_behavior="memory_answer", must_include=["5000"],
        seed_memory="La seña es 5000.",
    )
    result = CaseResult(
        case=case, intent="rag", answer="La seña es 5000, según me indicaste.",
        retrieved=[], sources=[], candidate_sql="",
    )
    assert deterministic_failures(result) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_eval_cases.py tests/test_eval_checks.py -k memory_answer -q`
Expected: FAIL (`memory_answer` no está en `_BEHAVIORS`; `_validate` no exige `seed_memory`; el check no existe).

- [ ] **Step 3: Implement**

`cases.py`:
- Línea 9: `_BEHAVIORS = frozenset({"cited_answer", "abstain_no_sources", "sql_answer", "memory_answer"})`
- `EvalCase` (agregar campo, después de `seed_doc`): `seed_memory: str | None = None`
- `load_golden_set` (dentro del `EvalCase(...)`): `seed_memory=raw.get("seed_memory"),`
- `_validate` (agregar antes del `if case.category == "sql"`):

```python
    if case.expected_behavior == "memory_answer":
        if not case.seed_memory:
            raise ValueError(f"memory_answer requiere seed_memory en {case.question!r}")
        if not case.must_include:
            raise ValueError(f"memory_answer requiere must_include en {case.question!r}")
```

`checks.py::deterministic_failures` (agregar junto a los otros checks de behavior, ~línea 24):

```python
    if case.expected_behavior == "memory_answer" and result.sources:
        fails.append("memory_answer con sources (no debería citar documentos)")
```

`run.py::_score_case` (envolver `run_case` con seed/forget). Imports arriba: `from app.config import get_settings` y `from app.memory import long_term`. Cuerpo:

```python
async def _score_case(case: EvalCase) -> tuple[CaseOutcome, RagSample | None]:
    seeded_id = None
    if case.seed_memory:
        await long_term.ensure_memories_collection()
        seeded_id = await long_term.store(
            get_settings().practice_id,
            kind="hecho",
            content=case.seed_memory,
            source="explicito",
            salience=0.8,
        )
    try:
        result = await run_case(case)
    finally:
        if seeded_id:
            await long_term.forget(get_settings().practice_id, [seeded_id])
    failures = deterministic_failures(result)
    if case.category == "sql" and not failures and case.gold_sql:
        if not await execution_accuracy(case.gold_sql, result.candidate_sql):
            failures.append("execution-accuracy: result set != gold")
    sample: RagSample | None = None
    if case.expected_behavior == "cited_answer" and case.ground_truth:
        sample = RagSample(
            question=case.question,
            answer=result.answer,
            contexts=result.retrieved,
            ground_truth=case.ground_truth,
        )
    return CaseOutcome(question=case.question, category=case.category, failures=failures), sample
```

`golden_set.jsonl` (línea nueva al final):

```json
{"question": "¿hay que dejar una seña para reservar un turno?", "category": "rag", "intent": "rag", "expected_behavior": "memory_answer", "must_include": ["5000"], "seed_memory": "Para reservar un turno hay que dejar una seña de 5000 pesos."}
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_eval_cases.py tests/test_eval_checks.py -q`
Expected: PASS.

- [ ] **Step 5: Run the FULL eval gate 3× to confirm non-flakiness (Ollama+PG+Qdrant, seed demo)**

Run (una vez el seed demo esté cargado — ver Final verification):
```bash
cd backend && .venv\Scripts\python -m app.eval.run
```
Expected: `exit=0` en **3 corridas consecutivas**, con `[PASS] ¿hay que dejar una seña para reservar un turno?` y las 5 métricas sin `REGRESION` (el caso `memory_answer` no produce `RagSample`, así que el baseline no se mueve).

> **Criterio de aceptación / fallback:** si el caso flaquea (router lo saca de `rag`, o la síntesis memory-only no incluye "5000") en cualquiera de las 3 corridas, **NO** commitear la línea de `golden_set.jsonl`; dejar el soporte de framework (`memory_answer` + `seed_memory` + seed/forget) commiteado para uso futuro y anotar el caso como cubierto por el e2e-llm de la Task 6. Informar al usuario el resultado de las 3 corridas.

- [ ] **Step 6: Commit**

```bash
git add backend/app/eval/cases.py backend/app/eval/checks.py backend/app/eval/run.py backend/app/eval/golden_set.jsonl backend/tests/test_eval_cases.py backend/tests/test_eval_checks.py
git commit -m "test(rag-memoria): caso memory_answer en el eval-gate (cierra Task 11)"
```

---

## Final verification (Definition of Done)

Antes de considerar la rama lista para review whole-branch + merge:

- [ ] **Lint/format/types (backend):**
  ```bash
  cd backend && .venv\Scripts\python -m ruff format . && .venv\Scripts\python -m ruff check . && .venv\Scripts\python -m mypy app/
  ```
  Expected: format OK, `ruff check` sin errores, `mypy` **Success (55 files)** (VERDE).

- [ ] **Gate `-m "not llm"` completo (docker arriba):**
  ```bash
  cd backend && .venv\Scripts\python -m pytest tests -m "not llm" -q
  ```
  Expected: **378 + nuevos**, 0 fallos.

- [ ] **e2e-llm de este fast-follow (Ollama arriba):**
  ```bash
  cd backend && .venv\Scripts\python -m pytest tests/test_rag_memory_e2e_llm.py -q
  ```
  Expected: PASS (memory-only no abstiene; precedencia "90" presente).

- [ ] **Eval gate (Ollama+PG+Qdrant; sembrar demo primero, porque `-m "not llm"` wipea el Qdrant compartido):**
  ```bash
  # desde la raíz del repo:
  backend\.venv\Scripts\python backend\seed_demo.py
  # y el gate (cwd backend, para el import de app.eval):
  cd backend && .venv\Scripts\python -m app.eval.run
  ```
  Expected: `exit=0`, sin `REGRESION` (baseline intacto: 5 métricas), caso de memoria PASS (o de-scopeado por el fallback del Task 7 Step 5).

- [ ] **Smoke navegador** (Ollama + docker + schema/seed + spaCy `es_core_news_md`; front `:3100`, backend `dev.py` en `:8000`; **re-sembrar `seed_demo.py` antes**):
  1. Contar un dato — "la seña para reservar sale $5000" — y luego preguntar *"¿cuánto hay que dejar de seña?"* → responde el valor (no abstiene).
  2. Merge/precedencia con el `protocolo` demo: decir *"en realidad ahora la primera consulta dura 90 minutos"* y preguntar *"¿cuánto dura la primera consulta?"* → lidera con 90 (memoria) y menciona los 60 del protocolo.
  3. Pregunta genuinamente documental sin memoria relacionada → sigue citando `[n]` como hoy (doc-only intacto).
  4. Una escritura CRM ("agendá un turno…") **sigue abriendo la ConfirmCard** (HITL intacto).

> **Recordatorio operativo:** si el chat da 500, chequear que `dev.py` esté arriba en `:8000` (`/health`) — el proxy de Next da 500 si el upstream está caído. Muchos edits rápidos pueden colgar el hot-reload de uvicorn → matar el árbol del reloader y relanzar `dev.py`.
