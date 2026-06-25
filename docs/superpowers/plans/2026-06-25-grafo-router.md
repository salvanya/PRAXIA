# Esqueleto del grafo LangGraph + router semántico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el flujo RAG plano de Fase 0 por una máquina de estados LangGraph con router semántico que clasifica la intención y deriva al subgrafo correspondiente, dejando el esqueleto listo para enchufar CRAG, NL2SQL, tools y guardrails en slices posteriores.

**Architecture:** Nuevo paquete `backend/app/graph/` (`state`, `router`, `nodes`, `edges`, `build`). El `/chat` invoca el grafo compilado y streamea por `stream_mode="custom"`: cada nodo de cara al usuario escribe tokens/fuentes vía el stream writer de LangGraph; el router (LLM e4b con salida estructurada) es interno y no escribe al usuario. Checkpointer `AsyncPostgresSaver` montado en el lifespan (thread_id server-side por request); los tests caen a un grafo por defecto sin checkpointer.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, `langgraph-checkpoint-postgres`, `langchain-ollama` (ChatOllama → Ollama local), `sse-starlette`, pytest (asyncio auto mode).

## Global Constraints

- Inferencia 100% local vía Ollama (`http://localhost:11434`). Prohibido llamar APIs externas. (CLAUDE.md §0)
- Router/jueces usan `gemma4:e4b`; razonamiento usa `gemma4:12b`. (CLAUDE.md §1)
- Salida estructurada obligatoria por decodificación restringida; prohibido parsear intents con regex. (CLAUDE.md §4)
- Toda lectura filtra por `practice_id` (multi-tenant). (CLAUDE.md §0.5)
- Escrituras solo por tools con `interrupt`; en este slice NO se escribe nada (los stubs no tocan la DB). (CLAUDE.md §4)
- Contrato SSE externo intacto: eventos `token` / `sources` / `done` con `[DONE]`. (no romper el frontend)
- DoD: `ruff check .` + `ruff format .` + `mypy app/` + `pytest -q` verdes; sin red saliente fuera de Ollama/PG/Qdrant. (CLAUDE.md §6)
- Commits LIMPIOS, sin ninguna atribución a Claude/Anthropic. (CLAUDE.md §6)
- ruff: line-length 100, reglas E,F,I,UP,B. mypy: `disallow_untyped_defs=true` (toda def anotada).
- `Chunk` TypedDict ya existe en `app/models.py` con campos: `text, page, chunk_index, document_id, title, doc_type`.
- `synthesize_stream(query, chunks, llm=None)`, `build_sources(chunks)`, `ABSTAIN_MESSAGE`, `ollama_available()` y `_default_llm()` viven en `app/rag/synthesize.py` y NO se modifican.

---

### Task 1: Dependencias + AgentState + scaffolding del paquete

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/graph/__init__.py`
- Create: `backend/app/graph/state.py`
- Test: `backend/tests/test_state.py`

**Interfaces:**
- Produces:
  - `AgentState` (TypedDict) con claves: `messages`, `practice_id`, `thread_id`, `intent`, `retrieved`, `sources`.
  - `new_state(message: str, practice_id: str, thread_id: str) -> AgentState`
  - `last_user_text(state: AgentState) -> str`

- [ ] **Step 1: Agregar dependencias**

En `backend/requirements.txt`, agregá estas dos líneas después de `langchain-ollama==0.2.*`:

```
langgraph==0.2.*
langgraph-checkpoint-postgres==2.*
```

Instalá en el venv del proyecto:

Run: `backend/.venv/Scripts/python -m pip install "langgraph==0.2.*" "langgraph-checkpoint-postgres==2.*"`
Expected: instala langgraph + langgraph-checkpoint-postgres + psycopg sin error.

- [ ] **Step 2: Escribir el test que falla**

Crear `backend/tests/test_state.py`:

```python
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.state import last_user_text, new_state


def test_new_state_has_minimal_shape():
    s = new_state("hola", practice_id="p-1", thread_id="t-1")
    assert s["practice_id"] == "p-1"
    assert s["thread_id"] == "t-1"
    assert s["intent"] == ""
    assert s["retrieved"] == []
    assert s["sources"] == []
    assert len(s["messages"]) == 1
    assert isinstance(s["messages"][0], HumanMessage)
    assert s["messages"][0].content == "hola"


def test_last_user_text_returns_latest_human_message():
    s = new_state("primera", practice_id="p-1", thread_id="t-1")
    s["messages"].append(AIMessage(content="respuesta"))
    s["messages"].append(HumanMessage(content="segunda"))
    assert last_user_text(s) == "segunda"


def test_last_user_text_empty_when_no_human():
    s = new_state("x", practice_id="p", thread_id="t")
    s["messages"] = [AIMessage(content="solo asistente")]
    assert last_user_text(s) == ""
```

- [ ] **Step 3: Verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_state.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.graph.state'`.

- [ ] **Step 4: Implementar `state.py` y `__init__.py`**

Crear `backend/app/graph/__init__.py` vacío:

```python
```

Crear `backend/app/graph/state.py`:

```python
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph.message import add_messages

from app.models import Chunk


class AgentState(TypedDict):
    """State tipado y mínimo del grafo (CLAUDE.md §4).

    Campos declarados para slices posteriores (plan, candidate_sql,
    proposed_action, judge_scores, memories, running_summary) se agregarán
    cuando su slice los escriba; se mantiene el state chico a propósito.
    """

    messages: Annotated[list, add_messages]
    practice_id: str
    thread_id: str
    intent: str
    retrieved: list[Chunk]
    sources: list[dict]


def new_state(message: str, practice_id: str, thread_id: str) -> AgentState:
    return {
        "messages": [HumanMessage(content=message)],
        "practice_id": practice_id,
        "thread_id": thread_id,
        "intent": "",
        "retrieved": [],
        "sources": [],
    }


def last_user_text(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""
```

- [ ] **Step 5: Verificar que pasa + lint/types**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_state.py -q`
Expected: PASS (3 tests).

Run: `cd backend && .venv/Scripts/python -m ruff check app/graph tests/test_state.py && .venv/Scripts/python -m ruff format app/graph tests/test_state.py && .venv/Scripts/python -m mypy app/graph`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/graph/ backend/tests/test_state.py
git commit -m "feat(graph): AgentState minimo + dependencias langgraph"
```

---

### Task 2: Router semántico (e4b + salida estructurada)

**Files:**
- Create: `backend/app/graph/router.py`
- Test: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: `AgentState`, `last_user_text` (Task 1).
- Produces:
  - `INTENTS: tuple[str, ...]` = `("rag", "sql", "action", "chitchat", "out_of_scope")`
  - `RouterDecision` (pydantic BaseModel) con campo `intent: Literal[...]`.
  - `async def classify_intent(message: str, llm: Any = None) -> str`
  - `async def router_node(state: AgentState) -> dict` → `{"intent": <str>}`

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_router.py`:

```python
import pytest

from app.graph import router
from app.graph.state import new_state


class FakeStructured:
    def __init__(self, intent: str):
        self._intent = intent

    async def ainvoke(self, _messages):
        return router.RouterDecision(intent=self._intent)


class FakeRouterLLM:
    def __init__(self, intent: str):
        self._intent = intent

    def with_structured_output(self, _schema):
        return FakeStructured(self._intent)


async def test_classify_intent_returns_enum_value():
    intent = await router.classify_intent("hola", llm=FakeRouterLLM("chitchat"))
    assert intent == "chitchat"


async def test_router_node_sets_intent_from_last_human():
    state = new_state("¿cuántos turnos esta semana?", practice_id="p", thread_id="t")
    # inyectamos un llm fake vía monkeypatch del factory interno
    patch = router._router_llm
    router._router_llm = lambda: FakeRouterLLM("sql")  # type: ignore[assignment]
    try:
        out = await router.router_node(state)
    finally:
        router._router_llm = patch  # type: ignore[assignment]
    assert out == {"intent": "sql"}


def test_intents_tuple_is_the_contract():
    assert router.INTENTS == ("rag", "sql", "action", "chitchat", "out_of_scope")


@pytest.mark.llm
@pytest.mark.integration
async def test_real_e4b_classifies_greeting_as_chitchat():
    intent = await router.classify_intent("hola, ¿cómo va?")
    assert intent == "chitchat"
```

- [ ] **Step 2: Verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_router.py -q -m "not llm"`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.graph.router'`.

- [ ] **Step 3: Implementar `router.py`**

Crear `backend/app/graph/router.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel

from app.config import get_settings
from app.graph.state import AgentState, last_user_text

INTENTS: tuple[str, ...] = ("rag", "sql", "action", "chitchat", "out_of_scope")

ROUTER_PROMPT = (
    "Sos el router de un CRM conversacional para prácticas profesionales (clínicas, "
    "odontología, psicología, tutorías). Clasificá el mensaje del usuario en UNA intención:\n"
    "- rag: pregunta cuya respuesta está en documentos subidos (protocolos, fichas, informes). "
    'Ej: "¿cuánto dura la primera consulta?", "¿qué dice el protocolo de cancelación?".\n'
    "- sql: pregunta sobre datos estructurados de la práctica (turnos, clientes, agenda, "
    'métricas). Ej: "¿cuántos turnos tengo esta semana?", "listá los clientes activos".\n'
    "- action: pide ejecutar una acción que modifica datos (crear/editar/cancelar). "
    'Ej: "agendá un turno para mañana", "marcá a Juan como inactivo".\n'
    "- chitchat: saludo o charla trivial sin pedido concreto. "
    'Ej: "hola", "gracias", "¿cómo estás?".\n'
    "- out_of_scope: fuera del dominio de la práctica (cocina, política, código, etc.). "
    'Ej: "¿cuál es la capital de Francia?", "escribime un poema".\n'
    "Respondé solo con la intención."
)


class RouterDecision(BaseModel):
    intent: Literal["rag", "sql", "action", "chitchat", "out_of_scope"]


def _router_llm() -> Any:
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(model="gemma4:e4b", base_url=s.ollama_base_url, temperature=0.0)


async def classify_intent(message: str, llm: Any = None) -> str:
    llm = llm or _router_llm()
    structured = llm.with_structured_output(RouterDecision)
    decision: RouterDecision = await structured.ainvoke(
        [("system", ROUTER_PROMPT), ("human", message)]
    )
    return decision.intent


async def router_node(state: AgentState) -> dict:
    intent = await classify_intent(last_user_text(state), llm=_router_llm())
    return {"intent": intent}
```

- [ ] **Step 4: Verificar que pasan los unit (no-llm)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_router.py -q -m "not llm"`
Expected: PASS (3 tests; el `-m llm` queda deseleccionado).

- [ ] **Step 5: Lint + types**

Run: `cd backend && .venv/Scripts/python -m ruff check app/graph/router.py tests/test_router.py && .venv/Scripts/python -m ruff format app/graph/router.py tests/test_router.py && .venv/Scripts/python -m mypy app/graph`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/graph/router.py backend/tests/test_router.py
git commit -m "feat(graph): router semantico e4b con salida estructurada"
```

---

### Task 3: Nodos (rag, chitchat, scope_reject, stubs) + streaming writer

**Files:**
- Create: `backend/app/graph/nodes.py`
- Test: `backend/tests/test_nodes.py`

**Interfaces:**
- Consumes: `AgentState`, `last_user_text` (Task 1); `retrieve` de `app.rag.retrieve`; `synthesize_stream`, `build_sources`, `ABSTAIN_MESSAGE` de `app.rag.synthesize`.
- Produces (todas `async def (state: AgentState) -> dict`):
  - `rag_node`, `chitchat_node`, `scope_reject_node`, `sql_stub`, `action_stub`
  - helpers de streaming: `write_token(text: str) -> None`, `write_sources(sources: list[dict]) -> None`
  - `STUB_MESSAGE: str`, `SCOPE_MESSAGE: str`
  - factory monkeypatcheable: `_chitchat_llm() -> Any` (ChatOllama 12b)

**Convención del stream (la lee el endpoint en Task 5):** cada nodo escribe dicts vía el stream writer de LangGraph:
- token de texto: `{"kind": "token", "text": <str>}`
- fuentes (solo rag): `{"kind": "sources", "sources": <list[dict]>}`

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_nodes.py`. Los nodos se prueban a través de un grafo de un solo nodo compilado y consumido con `stream_mode="custom"` (así se ejercita el stream writer real):

```python
from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import AgentState, new_state
from app.models import Chunk


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run(node, state):
    """Devuelve (tokens_concatenados, sources, parche_final)."""
    graph = _one_node_graph(node)
    tokens = ""
    sources: list = []
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
        elif chunk["kind"] == "sources":
            sources = chunk["sources"]
    return tokens, sources


def _chunk() -> Chunk:
    return Chunk(
        text="La primera consulta dura 60 minutos.",
        page=2,
        chunk_index=0,
        document_id="doc-1",
        title="Protocolo",
        doc_type="protocolo",
    )


async def test_scope_reject_streams_fixed_message_no_sources():
    tokens, sources = await _run(nodes.scope_reject_node, new_state("capital de Francia", "p", "t"))
    assert tokens == nodes.SCOPE_MESSAGE
    assert sources == []


async def test_sql_stub_streams_not_available():
    tokens, sources = await _run(nodes.sql_stub, new_state("cuántos turnos", "p", "t"))
    assert tokens == nodes.STUB_MESSAGE
    assert sources == []


async def test_action_stub_streams_not_available():
    tokens, _ = await _run(nodes.action_stub, new_state("agendá turno", "p", "t"))
    assert tokens == nodes.STUB_MESSAGE


async def test_rag_node_streams_tokens_and_sources(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return [_chunk()]

    async def fake_synth(query, chunks, llm=None):
        for piece in ["Según ", "el protocolo ", "[1]."]:
            yield piece

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)
    monkeypatch.setattr(nodes, "synthesize_stream", fake_synth)

    tokens, sources = await _run(nodes.rag_node, new_state("¿cuánto dura?", "p", "t"))
    assert "[1]" in tokens
    assert sources == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]


async def test_rag_node_abstains_without_chunks(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)

    tokens, sources = await _run(nodes.rag_node, new_state("algo raro", "p", "t"))
    assert tokens == nodes.ABSTAIN_MESSAGE
    assert sources == []


async def test_chitchat_streams_with_fake_llm(monkeypatch):
    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            for token in ["¡Hola! ", "¿En qué ", "te ayudo?"]:
                yield FakeMsg(token)

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())

    tokens, sources = await _run(nodes.chitchat_node, new_state("hola", "p", "t"))
    assert "Hola" in tokens
    assert sources == []
```

- [ ] **Step 2: Verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_nodes.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.graph.nodes'`.

- [ ] **Step 3: Implementar `nodes.py`**

Crear `backend/app/graph/nodes.py`:

```python
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.config import get_settings
from app.graph.state import AgentState, last_user_text
from app.rag.retrieve import retrieve
from app.rag.synthesize import ABSTAIN_MESSAGE, build_sources, synthesize_stream

STUB_MESSAGE = "Esa función todavía no está disponible (próximo slice)."
SCOPE_MESSAGE = (
    "Solo puedo ayudarte con la información y los datos de tu práctica. "
    "¿Querés que busque algo en tus documentos o tu agenda?"
)

CHITCHAT_SYSTEM = (
    "Sos el asistente de una práctica profesional. Respondé saludos y charla trivial "
    "en español, breve y cordial. No inventes datos de la práctica."
)


def write_token(text: str) -> None:
    if text:
        get_stream_writer()({"kind": "token", "text": text})


def write_sources(sources: list[dict]) -> None:
    get_stream_writer()({"kind": "sources", "sources": sources})


def _chitchat_llm() -> Any:
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(model=s.ollama_model, base_url=s.ollama_base_url, temperature=0.3)


async def rag_node(state: AgentState) -> dict:
    query = last_user_text(state)
    chunks = await retrieve(query, practice_id=state["practice_id"])
    if not chunks:
        write_token(ABSTAIN_MESSAGE)
        write_sources([])
        return {"retrieved": [], "sources": [], "messages": [AIMessage(content=ABSTAIN_MESSAGE)]}

    full = ""
    async for piece in synthesize_stream(query, chunks):
        write_token(piece)
        full += piece
    sources = build_sources(chunks)
    write_sources(sources)
    return {"retrieved": chunks, "sources": sources, "messages": [AIMessage(content=full)]}


async def chitchat_node(state: AgentState) -> dict:
    llm = _chitchat_llm()
    messages = [("system", CHITCHAT_SYSTEM), ("human", last_user_text(state))]
    full = ""
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            write_token(text)
            full += text
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=full)]}


async def scope_reject_node(state: AgentState) -> dict:
    write_token(SCOPE_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=SCOPE_MESSAGE)]}


async def sql_stub(state: AgentState) -> dict:
    write_token(STUB_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=STUB_MESSAGE)]}


async def action_stub(state: AgentState) -> dict:
    write_token(STUB_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=STUB_MESSAGE)]}
```

- [ ] **Step 4: Verificar que pasa**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_nodes.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + types**

Run: `cd backend && .venv/Scripts/python -m ruff check app/graph/nodes.py tests/test_nodes.py && .venv/Scripts/python -m ruff format app/graph/nodes.py tests/test_nodes.py && .venv/Scripts/python -m mypy app/graph`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/graph/nodes.py backend/tests/test_nodes.py
git commit -m "feat(graph): nodos rag/chitchat/scope/stubs con streaming writer"
```

---

### Task 4: Ensamblado del grafo (edges + build)

**Files:**
- Create: `backend/app/graph/edges.py`
- Create: `backend/app/graph/build.py`
- Test: `backend/tests/test_graph.py`

**Interfaces:**
- Consumes: `router_node` (Task 2); todos los nodos (Task 3); `AgentState` (Task 1).
- Produces:
  - `route(state: AgentState) -> str` (en `edges.py`): devuelve el nombre del nodo destino; default `"scope_reject"` si el intent no está en `INTENTS`.
  - `build_graph(checkpointer: Any = None) -> Any` (en `build.py`): compila y devuelve el grafo.
  - `get_default_graph() -> Any` (en `build.py`, `lru_cache`): grafo sin checkpointer para tests/fallback.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_graph.py`:

```python
import pytest

from app.graph import build, edges, nodes, router
from app.graph.state import new_state


def test_route_maps_intents_to_nodes():
    assert edges.route({"intent": "rag"}) == "rag"  # type: ignore[arg-type]
    assert edges.route({"intent": "sql"}) == "sql_stub"  # type: ignore[arg-type]
    assert edges.route({"intent": "action"}) == "action_stub"  # type: ignore[arg-type]
    assert edges.route({"intent": "chitchat"}) == "chitchat"  # type: ignore[arg-type]
    assert edges.route({"intent": "out_of_scope"}) == "scope_reject"  # type: ignore[arg-type]


def test_route_defaults_to_scope_reject_on_unknown():
    assert edges.route({"intent": "garbage"}) == "scope_reject"  # type: ignore[arg-type]


async def _run_full(monkeypatch, message, intent):
    monkeypatch.setattr(router, "classify_intent", lambda *_a, **_k: _aval(intent))
    graph = build.build_graph()
    tokens = ""
    sources: list = []
    async for chunk in graph.astream(new_state(message, "p", "t"), stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
        elif chunk["kind"] == "sources":
            sources = chunk["sources"]
    return tokens, sources


async def _aval(value):
    return value


async def test_graph_routes_sql_to_stub(monkeypatch):
    tokens, sources = await _run_full(monkeypatch, "¿cuántos turnos?", "sql")
    assert tokens == nodes.STUB_MESSAGE
    assert sources == []


async def test_graph_routes_out_of_scope_to_safe_answer(monkeypatch):
    tokens, _ = await _run_full(monkeypatch, "capital de Francia", "out_of_scope")
    assert tokens == nodes.SCOPE_MESSAGE


async def test_graph_routes_rag(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)
    tokens, sources = await _run_full(monkeypatch, "¿qué dice el protocolo?", "rag")
    assert tokens == nodes.ABSTAIN_MESSAGE


def test_get_default_graph_is_cached():
    assert build.get_default_graph() is build.get_default_graph()
```

Nota: `classify_intent` se monkeypatchea con una lambda que devuelve una corutina (`_aval`), porque `router_node` la invoca con `await`.

- [ ] **Step 2: Verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_graph.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.graph.edges'`.

- [ ] **Step 3: Implementar `edges.py`**

Crear `backend/app/graph/edges.py`:

```python
from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_stub",
    "action": "action_stub",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")
```

- [ ] **Step 4: Implementar `build.py`**

Crear `backend/app/graph/build.py`:

```python
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.edges import route
from app.graph.nodes import (
    action_stub,
    chitchat_node,
    rag_node,
    scope_reject_node,
    sql_stub,
)
from app.graph.router import router_node
from app.graph.state import AgentState

_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_stub", "action_stub")


def build_graph(checkpointer: Any = None) -> Any:
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("rag", rag_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("scope_reject", scope_reject_node)
    g.add_node("sql_stub", sql_stub)
    g.add_node("action_stub", action_stub)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route,
        {
            "rag": "rag",
            "chitchat": "chitchat",
            "scope_reject": "scope_reject",
            "sql_stub": "sql_stub",
            "action_stub": "action_stub",
        },
    )
    for node in _LEAF_NODES:
        g.add_edge(node, END)

    return g.compile(checkpointer=checkpointer)


@lru_cache
def get_default_graph() -> Any:
    """Grafo sin checkpointer (tests / fallback cuando el lifespan no corrió)."""
    return build_graph(checkpointer=None)
```

- [ ] **Step 5: Verificar que pasa**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_graph.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint + types**

Run: `cd backend && .venv/Scripts/python -m ruff check app/graph tests/test_graph.py && .venv/Scripts/python -m ruff format app/graph tests/test_graph.py && .venv/Scripts/python -m mypy app/graph`
Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add backend/app/graph/edges.py backend/app/graph/build.py backend/tests/test_graph.py
git commit -m "feat(graph): ensamblado del StateGraph con routing condicional"
```

---

### Task 5: Integrar `/chat` con el grafo + checkpointer en el lifespan

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py`
- Test (sin cambios, debe seguir verde): `backend/tests/test_e2e_llm.py`

**Interfaces:**
- Consumes: `build_graph`, `get_default_graph` (Task 4); `new_state` (Task 1); `ollama_available` (sin cambios, de `app.rag.synthesize`).
- Produces: `/chat` invoca el grafo y streamea por `stream_mode="custom"`, mapeando los payloads `{"kind": "token"|"sources"}` a eventos SSE `token`/`sources`/`done`. El probe Ollama→503 se evalúa SIEMPRE (el router e4b necesita Ollama).

- [ ] **Step 1: Adaptar los tests existentes de la API (que fallarán)**

En `backend/tests/test_api.py`:

(a) Reemplazá el fixture `fake_llm` para mockear además el router y el chitchat (el `/chat` ahora pasa por el grafo). Cambiá el cuerpo del fixture por:

```python
@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    class FakeLLM:
        async def astream(self, messages):
            for token in ["Respuesta ", "citada ", "[1]."]:
                yield FakeChunkMsg(token)

    monkeypatch.setattr(synthesize, "_default_llm", lambda: FakeLLM())
    # El router e4b no debe llamar a Ollama en tests no-LLM: forzamos intent rag.
    from app.graph import router

    async def fake_classify(*_args, **_kwargs):
        return "rag"

    monkeypatch.setattr(router, "classify_intent", fake_classify)
```

(b) En `test_ingest_then_chat_streams_sources`, antes del `c.stream(...)`, asegurá que el probe de Ollama no corte (es integration con Ollama real, así que no hace falta mock; queda igual). El test sigue válido tal cual.

(c) Reemplazá `test_chat_returns_503_when_ollama_down` por (ya no se mockea `retrieve` en main):

```python
async def test_chat_returns_503_when_ollama_down(monkeypatch):
    from app import main

    async def fake_unavailable():
        return False

    monkeypatch.setattr(main, "ollama_available", fake_unavailable)

    async with await _client() as c:
        resp = await c.post("/chat", json={"message": "hola"})
    assert resp.status_code == 503
    assert "Ollama" in resp.json()["detail"]
```

- [ ] **Step 2: Verificar que fallan (no-llm, no-integration)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_api.py -q -m "not integration and not llm"`
Expected: FAIL — `test_chat_returns_503_when_ollama_down` falla porque `main` todavía importa/usa `retrieve` y el flujo viejo (o el grafo no está cableado).

- [ ] **Step 3: Reescribir `main.py`**

Reemplazá `backend/app/main.py` por:

```python
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db, vectorstore
from app.config import get_settings
from app.graph.build import build_graph, get_default_graph
from app.graph.state import new_state
from app.ingest.pipeline import ingest_document
from app.rag.synthesize import ollama_available

SUPPORTED_SUFFIXES = (".pdf", ".md", ".markdown", ".txt")


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    await vectorstore.ensure_collection()
    s = get_settings()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
        await saver.setup()
        app_.state.graph = build_graph(checkpointer=saver)
        yield


app = FastAPI(title="Praxia · Fase 1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),  # noqa: B008
    doc_type: str = Form("protocolo"),  # noqa: B008
    title: str = Form(...),  # noqa: B008
) -> dict[str, Any]:
    filename = file.filename or "documento"
    if not filename.lower().endswith(SUPPORTED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"Tipo no soportado: {filename}")
    data = await file.read()
    try:
        return dict(await ingest_document(data, filename, doc_type, title))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/documents")
async def documents() -> list[dict]:
    return await db.list_documents(get_settings().practice_id)


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    # El router e4b (y los nodos LLM) necesitan Ollama: si está caído, 503 amable
    # antes de abrir el stream SSE (preserva el fix de la limpieza pre-Fase 1).
    if not await ollama_available():
        raise HTTPException(
            status_code=503,
            detail="El asistente local (Ollama) no está disponible. "
            "Verificá que Ollama esté corriendo y volvé a intentar.",
        )

    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    s = get_settings()
    state = new_state(req.message, practice_id=s.practice_id, thread_id=str(uuid4()))
    config = {"configurable": {"thread_id": state["thread_id"]}}

    async def event_stream() -> AsyncIterator[dict]:
        async for chunk in graph.astream(state, config, stream_mode="custom"):
            kind = chunk.get("kind")
            if kind == "token":
                yield {"event": "token", "data": chunk["text"]}
            elif kind == "sources":
                yield {
                    "event": "sources",
                    "data": json.dumps(chunk["sources"], ensure_ascii=False),
                }
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_stream())
```

- [ ] **Step 4: Verificar la suite no-llm completa**

Run: `backend/.venv/Scripts/python -m pytest backend/tests -q -m "not llm and not integration"`
Expected: PASS — incluye `test_api.py` (health, 503, unsupported) y todos los de `graph/`.

- [ ] **Step 5: Suite de integración + llm (requiere docker compose + Ollama)**

Run: `docker compose up -d`
Run: `backend/.venv/Scripts/python -m pytest backend/tests -q -m "integration and not llm"`
Expected: PASS — `test_ingest_then_chat_streams_sources` rutea por el grafo (router mockeado a rag) y emite `token` + `sources`.

Run: `backend/.venv/Scripts/python -m pytest backend/tests -q -m "llm"`
Expected: PASS — `test_e2e_llm.py` (RAG real con cita + abstención real) y el caso real de router e4b siguen verdes. El smoke de §2 (`hola`→chitchat, documental→RAG con citas, estructurada→stub SQL) queda cubierto end-to-end.

- [ ] **Step 6: Lint + types de todo el backend**

Run: `cd backend && .venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format app tests && .venv/Scripts/python -m mypy app/`
Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(graph): /chat invoca el grafo con checkpointer Postgres y streaming custom"
```

---

## Self-Review

**Spec coverage:**
- Módulos `graph/{state,router,nodes,edges,build}` → Tasks 1-4. ✅
- `AgentState` mínimo con campos del slice → Task 1. ✅
- Router e4b + salida estructurada (sin regex) → Task 2. ✅
- Nodos rag (envuelve lo de hoy) / chitchat / scope_reject / stubs sql+action → Task 3. ✅
- Streaming SSE preservando contrato `token`/`sources`/`done`, router no se filtra al usuario → Tasks 3 (writer) + 5 (endpoint). Mecanismo: `stream_mode="custom"` (refina el `astream_events` del spec; mismo contrato externo, maneja uniformemente los nodos de texto fijo). ✅
- Checkpointer Postgres opción 2 (thread_id server-side, infra montada en lifespan) → Task 5. ✅
- Probe Ollama→503 amable preservado → Task 5. ✅
- Multi-tenant: `rag_node` filtra `retrieve` por `practice_id` del state → Task 3. ✅
- Sin escrituras (stubs no tocan DB) → Task 3. ✅
- Dependencias `langgraph` + `langgraph-checkpoint-postgres` → Task 1. ✅
- Testing (router, graph, chat SSE, smoke §2) → Tasks 2,3,4,5. ✅

**Placeholder scan:** sin TBD/TODO/"manejar edge cases"; todo el código está completo en cada step. ✅

**Type consistency:** `route` devuelve nombres de nodo (`sql_stub`/`action_stub`/`scope_reject`/`rag`/`chitchat`) consistentes entre `edges.py`, el dict de `add_conditional_edges` y `_LEAF_NODES`. `classify_intent`/`router_node` consistentes entre Tasks 2 y 4. Convención del stream `{"kind","text"/"sources"}` consistente entre `nodes.py` (Task 3) y `main.py` (Task 5). ✅

## Desviación registrada vs spec

El spec especificó `astream_events(version="v2")` filtrando por `metadata.langgraph_node`. El plan usa `stream_mode="custom"` con el stream writer de LangGraph: es más simple, no depende de filtrar por nombre de nodo, y maneja uniformemente los nodos de texto fijo (abstención, scope, stubs) que no emiten `on_chat_model_stream`. El contrato SSE externo (`token`/`sources`/`done`) es idéntico. Si preferís el mecanismo original del spec, se ajusta antes de ejecutar.
