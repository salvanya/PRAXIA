# Memoria de corto plazo + slot-filling — Plan de implementación (Slice 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `thread_id` estable end-to-end + slot-filling multi-turno que pregunta «¿cuál?» (cliente y turno) y resuelve en el turno siguiente, en lugar de abstenerse listando.

**Architecture:** El checkpointer Postgres (ya cableado) persiste el estado por `thread_id`. Un **entry condicional** bifurca `START → clarify` si hay `pending_clarification`, si no `→ router` (no se toca el router e4b). El `clarify_node` mapea la respuesta a un índice de candidato (`resolve_choice`, 12b) y **re-invoca el mismo `propose_X` con overrides** del slot resuelto; el resultado se procesa igual que un propose normal, encadenando cliente→turno. `confirm_action` y el front `ConfirmCard` no cambian.

**Tech Stack:** Python 3.11 · FastAPI · LangGraph (checkpointer + `interrupt`/`Command` + `aget_state`) · `langchain-ollama` (`gemma4:12b` extractores/choice, `gemma4:e4b` router/clasificador) · Next.js + `@assistant-ui/react` · pytest/vitest.

## Global Constraints

- **Local-first · $0 · privacidad** (CLAUDE.md §0): inferencia 100% local por Ollama; sin red saliente fuera de Ollama/Postgres/Qdrant locales.
- **Escrituras solo por tools parametrizadas detrás del `interrupt`** (HITL): ninguna rama del slot-filling escribe; solo arma `proposed_action → confirm_action`.
- **Aislamiento multi-tenant** por `practice_id` en toda resolución; los `candidates` vienen de los resolvers ya scoped.
- **Fail-closed**: respuesta que no mapea con seguridad → se descarta el pending y se pide reintento; nunca se elige por el usuario. El LLM devuelve un **índice** validado por rango; nunca toca IDs/SQL.
- **`with_structured_output` confiable solo para el 12b** (enteros/enums/IDs). El router/clasificador e4b siguen con `ainvoke` + text-parse (gotcha del `None` intermitente). `resolve_choice` usa el **12b**.
- **Lint:** correr `ruff format` **antes** de `ruff check` (E501 marca código plano largo pero exime `# type: ignore`). **mypy SIEMPRE** con `--config-file backend/pyproject.toml` (sin eso, falso-positivo `asyncpg [import-untyped]`).
- **Imports nuevos en archivos de tests EXISTENTES van al TOP** (ruff E402; `select=["E",...]` sin ignore).
- **Tests del front:** `npm --prefix frontend run test -- --run` (NO `npx --prefix frontend vitest run`).
- **Commits LIMPIOS**, sin ninguna atribución a Claude (CLAUDE.md §6). Autor = el usuario.
- **Comandos desde la raíz del repo.** Backend Python: `backend\.venv\Scripts\python`. En el harness PowerShell el cwd del tool puede ser `backend`; si un comando falla por path, ajustá el cwd o usá rutas relativas a `backend`.
- **Gate no-llm (no regresión):** `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` (hoy 206 passed; debe subir con los tests nuevos, nunca bajar).

## File Structure

```
backend/app/
├── graph/
│   ├── state.py          # +pending_clarification (TypedDict + new_state)          [T4]
│   ├── nodes.py          # +clarify_node, _handle_proposal_result, _numbered;       [T3 chitchat, T7]
│   │                     #  chitchat ve historial; confirm limpia proposed_action
│   ├── edges.py          # +entry_route; route_after_clarify = route_after_propose  [T7]
│   └── build.py          # +nodo "clarify"; entry condicional                       [T7]
├── agents/
│   ├── choice_agent.py   # NUEVO: Choice + resolve_choice                           [T5]
│   ├── action_agent.py   # +Clarification, ProposalResult.clarification,            [T4, T10]
│   │                     #  clarify_or_abstain_{client,appointment}; create unificado
│   ├── resolvers.py      # ClientResolution/AppointmentResolution +candidates       [T4]
│   ├── cancel_agent.py   # +client_override, appointment_override, clarification    [T6]
│   ├── reschedule_agent.py # idem cancel                                            [T8]
│   ├── interaction_agent.py# +client_override, clarification                        [T9]
│   └── update_client_agent.py # +client_override, clarification                     [T9]
├── main.py               # ChatRequest.thread_id; select_chat_input; /chat          [T1]
└── config.py             # +short_term_history_window                               [T3]
frontend/lib/
├── chatStream.ts         # streamChat(message, threadId)                            [T2]
└── runtime.ts            # threadId estable (useRef)                                [T2]
```

---

### Task 1: `thread_id` estable en el backend

**Files:**
- Modify: `backend/app/main.py` (ChatRequest, nuevo helper, `/chat`)
- Test: `backend/tests/test_chat_input.py` (crear)

**Interfaces:**
- Consumes: `new_state(message, practice_id, thread_id)` (existe), `graph.aget_state(config)` (LangGraph).
- Produces: `select_chat_input(snapshot_values: dict, message: str, practice_id: str, thread_id: str) -> Any` — primer turno (values vacío) → `new_state(...)` completo; turno siguiente → `{"messages": [HumanMessage(content=message)]}` (parche incremental que NO pisa el estado del checkpoint).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chat_input.py
from langchain_core.messages import HumanMessage

from app.main import select_chat_input


def test_first_turn_returns_full_initial_state() -> None:
    inp = select_chat_input({}, "hola", "pid", "tid")
    assert inp["thread_id"] == "tid" and inp["practice_id"] == "pid"
    assert len(inp["messages"]) == 1 and inp["messages"][0].content == "hola"
    assert inp["proposed_action"] is None


def test_subsequent_turn_returns_incremental_patch_only() -> None:
    inp = select_chat_input({"messages": ["prev"], "practice_id": "pid"}, "segundo", "pid", "tid")
    assert set(inp.keys()) == {"messages"}  # NO incluye los demás campos → no los pisa
    assert isinstance(inp["messages"][0], HumanMessage)
    assert inp["messages"][0].content == "segundo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_chat_input.py -q`
Expected: FAIL — `ImportError: cannot import name 'select_chat_input'`.

- [ ] **Step 3: Implement in `main.py`**

Add the import (top of file, junto a los otros `langchain`/`langgraph`):
```python
from langchain_core.messages import HumanMessage
```
Add `thread_id` to `ChatRequest`:
```python
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
```
Add the helper (after `ChatRequest`/`ResumeRequest`):
```python
def select_chat_input(
    snapshot_values: dict, message: str, practice_id: str, thread_id: str
) -> Any:
    """Primer turno (sin estado en el checkpoint) → state inicial completo; turno siguiente
    → parche incremental (solo el mensaje), para no pisar pending_clarification/proposed_action."""
    if snapshot_values:
        return {"messages": [HumanMessage(content=message)]}
    return new_state(message, practice_id=practice_id, thread_id=thread_id)
```
Replace the body of `/chat` (the `state = new_state(...)` / `config = ...` lines) with:
```python
    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    s = get_settings()
    thread_id = req.thread_id or str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values
    except Exception:  # noqa: BLE001 - sin checkpointer (fallback get_default_graph) → arranca limpio
        values = {}
    inp = select_chat_input(values, req.message, s.practice_id, thread_id)
    return EventSourceResponse(_sse_event_stream(graph, inp, config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_chat_input.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/main.py backend/tests/test_chat_input.py
backend\.venv\Scripts\python -m ruff check backend/app/main.py backend/tests/test_chat_input.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/main.py backend/tests/test_chat_input.py
git commit -m "feat(chat): thread_id estable; /chat elige input incremental vs inicial"
```

---

### Task 2: `thread_id` estable en el frontend

**Files:**
- Modify: `frontend/lib/chatStream.ts` (`streamChat` acepta `threadId`)
- Modify: `frontend/lib/runtime.ts` (genera/mantiene `threadId` estable)
- Test: `frontend/lib/chatStream.test.ts` (actualizar llamadas + agregar test de body)

**Interfaces:**
- Consumes: `streamSSE(url, body, signal)` (existe).
- Produces: `streamChat(message: string, threadId: string, signal?: AbortSignal)` — el body POST a `/api/chat` ahora es `{ message, thread_id: threadId }`.

- [ ] **Step 1: Update the failing test**

In `frontend/lib/chatStream.test.ts`, update the existing `streamChat(...)` calls to pass a thread id (the tests that call `streamChat("¿hola?")`, `streamChat("x")`, `streamChat("agendá")` → add `, "t1"` as 2nd arg), and add:
```typescript
test("streamChat posts message and thread_id", async () => {
  const fetchMock = vi.fn().mockResolvedValue(sseResponse(["event: done\ndata: [DONE]\n\n"]));
  vi.stubGlobal("fetch", fetchMock);
  for await (const _ of streamChat("hola", "tid-1")) { /* drain */ }
  expect(fetchMock).toHaveBeenCalledWith("/api/chat", expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ message: "hola", thread_id: "tid-1" }),
  }));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test -- --run chatStream`
Expected: FAIL — `streamChat` aún manda `{ message }` (falta `thread_id`); type error por el 2º arg.

- [ ] **Step 3: Implement**

`frontend/lib/chatStream.ts` — replace `streamChat`:
```typescript
export function streamChat(
  message: string,
  threadId: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  return streamSSE("/api/chat", { message, thread_id: threadId }, signal);
}
```

`frontend/lib/runtime.ts` — import `useRef` and generate a stable id:
```typescript
import { useMemo, useRef } from "react";
```
Inside `useChatRuntime`, before `useMemo`:
```typescript
  const threadIdRef = useRef<string>();
  if (!threadIdRef.current) threadIdRef.current = crypto.randomUUID();
```
In the adapter, change the stream call:
```typescript
          for await (const ev of streamChat(query, threadIdRef.current!, abortSignal)) {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix frontend run test -- --run`
Expected: PASS (all chatStream tests, incl. the new one).

- [ ] **Step 5: Lint, build, commit**

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
git add frontend/lib/chatStream.ts frontend/lib/runtime.ts frontend/lib/chatStream.test.ts
git commit -m "feat(frontend): el chat manda un thread_id estable por conversación"
```

---

### Task 3: `chitchat` contextual (memoria conversacional) + config

**Files:**
- Modify: `backend/app/config.py` (+`short_term_history_window`)
- Modify: `backend/app/graph/nodes.py` (`chitchat_node` ve el historial; import `HumanMessage`)
- Test: `backend/tests/test_config.py` (+1 assert), `backend/tests/test_nodes.py` (+1 test)

**Interfaces:**
- Consumes: `get_settings().short_term_history_window` (esta task lo crea), `state["messages"]`.
- Produces: `chitchat_node` pasa al LLM `[("system", CHITCHAT_SYSTEM), *últimos N (role, text)]`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_config.py` add inside `test_appointment_config_defaults` (or a new test):
```python
def test_short_term_history_window_default() -> None:
    assert get_settings().short_term_history_window == 10
```
In `backend/tests/test_nodes.py` add (the imports `HumanMessage, AIMessage` already exist at top via `new_state` usage — if not present, add `from langchain_core.messages import AIMessage, HumanMessage` at the TOP):
```python
async def test_chitchat_includes_recent_history(monkeypatch):
    captured = {}

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("ok")

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())
    state = new_state("¿cómo me llamo?", "p", "t")
    from langchain_core.messages import AIMessage, HumanMessage
    state["messages"] = [
        HumanMessage(content="soy Ana"),
        AIMessage(content="¡Hola Ana!"),
        HumanMessage(content="¿cómo me llamo?"),
    ]
    await _run(nodes.chitchat_node, state)
    assert captured["messages"][0] == ("system", nodes.CHITCHAT_SYSTEM)
    assert ("human", "soy Ana") in captured["messages"]
    assert ("ai", "¡Hola Ana!") in captured["messages"]
```
(Move the `from langchain_core.messages import ...` to the TOP of the file to satisfy ruff E402; shown inline here only for clarity.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_config.py::test_short_term_history_window_default backend/tests/test_nodes.py::test_chitchat_includes_recent_history -q`
Expected: FAIL — attribute `short_term_history_window` missing; chitchat sends only the last message.

- [ ] **Step 3: Implement**

`backend/app/config.py` — add after `appt_name_match_limit`:
```python
    short_term_history_window: int = 10  # mensajes recientes que ve chitchat (ventana fija; running_summary = Fase 2)
```
`backend/app/graph/nodes.py` — add `HumanMessage` to the existing import:
```python
from langchain_core.messages import AIMessage, HumanMessage
```
Add a helper and rewrite `chitchat_node`:
```python
def _history_messages(state: AgentState, window: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in state["messages"][-window:]:
        text = getattr(m, "content", "")
        if not isinstance(text, str) or not text:
            continue
        out.append(("human" if isinstance(m, HumanMessage) else "ai", text))
    return out


async def chitchat_node(state: AgentState) -> dict:
    llm = _chitchat_llm()
    window = get_settings().short_term_history_window
    messages = [("system", CHITCHAT_SYSTEM), *_history_messages(state, window)]
    full = ""
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            write_token(text)
            full += text
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=full)]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_config.py backend/tests/test_nodes.py -q`
Expected: PASS (incl. the existing `test_chitchat_streams_with_fake_llm`, which still works with a single-message history).

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/config.py backend/app/graph/nodes.py backend/tests/test_config.py backend/tests/test_nodes.py
backend\.venv\Scripts\python -m ruff check backend/app/config.py backend/app/graph/nodes.py backend/tests/test_config.py backend/tests/test_nodes.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/config.py backend/app/graph/nodes.py backend/tests/test_config.py backend/tests/test_nodes.py
git commit -m "feat(chitchat): usa el historial reciente del thread (memoria conversacional)"
```

---

### Task 4: `Clarification` + `ProposalResult.clarification` + resolvers exponen `candidates` + helpers

**Files:**
- Modify: `backend/app/agents/action_agent.py` (+`Clarification`, +`ProposalResult.clarification`, +2 helpers, +import resolvers)
- Modify: `backend/app/agents/resolvers.py` (+`candidates` en ambos dataclasses; poblar en ramas ambiguas)
- Modify: `backend/app/graph/state.py` (+`pending_clarification`)
- Test: `backend/tests/test_resolvers.py`, `backend/tests/test_state.py`, `backend/tests/test_action_agent.py`

**Interfaces:**
- Produces:
  - `Clarification(stage: str, candidates: list[dict], prompt: str)` (dataclass, en `action_agent`).
  - `ProposalResult.clarification: Clarification | None = None`.
  - `ClientResolution.candidates: list[dict]`, `AppointmentResolution.candidates: list[dict]` (default `[]`, poblado en ramas `*_ambiguous`).
  - `clarify_or_abstain_client(res: ClientResolution) -> ProposalResult`, `clarify_or_abstain_appointment(res: AppointmentResolution) -> ProposalResult` (en `action_agent`): abstención dura, con `clarification` seteado solo si el reason es `*_ambiguous`.
  - `AgentState["pending_clarification"]: dict | None`; `new_state(...)` lo inicializa a `None`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_state.py` — add:
```python
def test_new_state_inits_pending_clarification_none() -> None:
    assert new_state("hola", "pid", "tid")["pending_clarification"] is None
```
`backend/tests/test_resolvers.py` — strengthen `test_ambiguous` and `test_appt_many_no_hint_ambiguous`, and add a not-ambiguous candidates check:
```python
# inside test_ambiguous, after the existing asserts:
    assert [c["id"] for c in r.candidates] == ["1", "2"]

# inside test_appt_many_no_hint_ambiguous, after the existing asserts:
    assert [a["id"] for a in r.candidates] == ["a1", "a2"]

# new test:
async def test_single_client_has_no_candidates(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana"}])
    r = await resolvers.resolve_single_client("pid", "Ana", limit=5)
    assert r.candidates == []
```
`backend/tests/test_action_agent.py` — add (imports at TOP):
```python
from app.agents.action_agent import (
    Clarification,
    ProposalResult,
    clarify_or_abstain_appointment,
    clarify_or_abstain_client,
)
from app.agents.resolvers import AppointmentResolution, ClientResolution


def test_clarify_or_abstain_client_ambiguous_sets_clarification() -> None:
    res = ClientResolution(None, "Hay varios", "client_ambiguous", candidates=[{"id": "1"}, {"id": "2"}])
    pr = clarify_or_abstain_client(res)
    assert pr.abstained and pr.clarification is not None
    assert pr.clarification.stage == "client" and len(pr.clarification.candidates) == 2


def test_clarify_or_abstain_client_not_found_has_no_clarification() -> None:
    res = ClientResolution(None, "No encontré", "client_not_found")
    pr = clarify_or_abstain_client(res)
    assert pr.abstained and pr.clarification is None


def test_clarify_or_abstain_appointment_ambiguous_sets_stage() -> None:
    res = AppointmentResolution(None, "Varios turnos", "appointment_ambiguous", candidates=[{"id": "a1"}])
    pr = clarify_or_abstain_appointment(res)
    assert pr.clarification is not None and pr.clarification.stage == "appointment"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_state.py backend/tests/test_resolvers.py backend/tests/test_action_agent.py -q`
Expected: FAIL — `pending_clarification` KeyError; `candidates`/`clarification`/helpers undefined.

- [ ] **Step 3: Implement**

`backend/app/graph/state.py` — add field + init:
```python
    proposed_action: dict | None
    pending_clarification: dict | None
```
and in `new_state(...)` return dict add `"pending_clarification": None,`.

`backend/app/agents/resolvers.py` — add `field` to the dataclass import and `candidates`:
```python
from dataclasses import dataclass, field
```
```python
@dataclass
class ClientResolution:
    client: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
```
In `resolve_single_client`, the `len(clients) > 1` branch returns `ClientResolution(None, "...", "client_ambiguous", candidates=clients)`.
```python
@dataclass
class AppointmentResolution:
    appointment: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
```
In `resolve_single_appointment`, the `len(matches) > 1` branch returns `AppointmentResolution(None, "...", "appointment_ambiguous", candidates=matches)`.

`backend/app/agents/action_agent.py` — add import + `Clarification` + field + helpers:
```python
from app.agents.resolvers import AppointmentResolution, ClientResolution
```
```python
@dataclass
class Clarification:
    stage: str  # "client" | "appointment"
    candidates: list[dict[str, Any]]
    prompt: str  # encabezado humano ("Hay varios clientes…" / "…tiene varios turnos…")


@dataclass
class ProposalResult:
    proposed_action: dict[str, Any] | None
    abstained: bool
    message: str
    reason: str
    clarification: Clarification | None = None
```
```python
def clarify_or_abstain_client(res: ClientResolution) -> ProposalResult:
    clar = (
        Clarification("client", res.candidates, res.abstain_message)
        if res.abstain_reason == "client_ambiguous"
        else None
    )
    return ProposalResult(None, abstained=True, message=res.abstain_message,
                          reason=res.abstain_reason, clarification=clar)


def clarify_or_abstain_appointment(res: AppointmentResolution) -> ProposalResult:
    clar = (
        Clarification("appointment", res.candidates, res.abstain_message)
        if res.abstain_reason == "appointment_ambiguous"
        else None
    )
    return ProposalResult(None, abstained=True, message=res.abstain_message,
                          reason=res.abstain_reason, clarification=clar)
```

- [ ] **Step 4: Run tests + the full no-llm gate (state change ripples widely)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_state.py backend/tests/test_resolvers.py backend/tests/test_action_agent.py -q`
Expected: PASS.
Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (no-regresión: agregar un campo opcional al state y a los dataclasses no rompe call sites existentes).

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/action_agent.py backend/app/agents/resolvers.py backend/app/graph/state.py backend/tests/test_state.py backend/tests/test_resolvers.py backend/tests/test_action_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/action_agent.py backend/app/agents/resolvers.py backend/app/graph/state.py backend/tests/test_state.py backend/tests/test_resolvers.py backend/tests/test_action_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/action_agent.py backend/app/agents/resolvers.py backend/app/graph/state.py backend/tests/
git commit -m "feat(agents): Clarification + resolvers exponen candidates; pending_clarification en el state"
```

---

### Task 5: `resolve_choice` (mapeo respuesta → índice de candidato)

**Files:**
- Create: `backend/app/agents/choice_agent.py`
- Test: `backend/tests/test_choice_agent.py`

**Interfaces:**
- Consumes: `make_llm(model, temperature)` (existe).
- Produces: `resolve_choice(numbered: str, reply: str, *, n: int, gen_llm=None) -> int` — devuelve 1..n, o **0** si no es claro / error / fuera de rango. Modelo `Choice(choice: int)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_choice_agent.py
from app.agents import choice_agent
from app.agents.choice_agent import Choice


class _FakeStructured:
    def __init__(self, value):  # type: ignore[no-untyped-def]
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class FakeLLM:
    def __init__(self, value):  # type: ignore[no-untyped-def]
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


async def test_returns_valid_choice() -> None:
    r = await choice_agent.resolve_choice("1. A\n2. B", "la segunda", n=2, gen_llm=FakeLLM(Choice(choice=2)))
    assert r == 2


async def test_zero_when_unclear() -> None:
    r = await choice_agent.resolve_choice("1. A\n2. B", "no sé", n=2, gen_llm=FakeLLM(Choice(choice=0)))
    assert r == 0


async def test_out_of_range_is_zero() -> None:
    r = await choice_agent.resolve_choice("1. A", "el 5", n=1, gen_llm=FakeLLM(Choice(choice=5)))
    assert r == 0


async def test_exception_is_zero() -> None:
    r = await choice_agent.resolve_choice("1. A", "x", n=1, gen_llm=FakeLLM(RuntimeError("boom")))
    assert r == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_choice_agent.py -q`
Expected: FAIL — module `choice_agent` does not exist.

- [ ] **Step 3: Implement**

```python
# backend/app/agents/choice_agent.py
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.llm import make_llm


class Choice(BaseModel):
    choice: int  # 1..n elegido; 0 si no está claro


def _system_prompt() -> str:
    return (
        "Te doy una lista NUMERADA de opciones y la respuesta de un usuario. Devolvé el NÚMERO "
        "de la opción que el usuario eligió. Si la respuesta no identifica con claridad UNA sola "
        "opción (es ambigua, vacía o cambia de tema), devolvé 0. No inventes."
    )


def _choice_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


async def resolve_choice(numbered: str, reply: str, *, n: int, gen_llm: Any = None) -> int:
    """Mapea la respuesta del usuario a un índice 1..n (0 = no claro). Fail-closed: error o
    fuera de rango → 0. El 12B con structured int es confiable (entero acotado, como un id)."""
    llm = gen_llm or _choice_llm()
    structured = llm.with_structured_output(Choice)
    human = f"Opciones:\n{numbered}\n\nRespuesta del usuario: «{reply}»"
    try:
        result = await structured.ainvoke([("system", _system_prompt()), ("human", human)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo → no-mapea
        return 0
    if not isinstance(result, Choice):
        return 0
    return result.choice if 1 <= result.choice <= n else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_choice_agent.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/choice_agent.py backend/tests/test_choice_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/choice_agent.py backend/tests/test_choice_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/choice_agent.py backend/tests/test_choice_agent.py
git commit -m "feat(agents): resolve_choice mapea la respuesta del usuario a un índice de candidato"
```

---

### Task 6: `cancel_agent` — overrides (cliente + turno) + `clarification`

**Files:**
- Modify: `backend/app/agents/cancel_agent.py`
- Test: `backend/tests/test_cancel_agent.py`

**Interfaces:**
- Consumes: `clarify_or_abstain_client`, `clarify_or_abstain_appointment` (Task 4).
- Produces: `propose_cancellation(question, practice_id, *, now, gen_llm=None, client_override=None, appointment_override=None) -> ProposalResult` — con override salta la resolución correspondiente; ante ambigüedad devuelve `ProposalResult.clarification` (stage `client` o `appointment`).

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_cancel_agent.py`)

```python
async def test_client_override_skips_client_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _find_appts(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return [_appt()]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"})
    assert not called["clients"]
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"


async def test_client_ambiguous_returns_clarification(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "client"
    assert len(result.clarification.candidates) == 2


async def test_appointment_ambiguous_returns_clarification(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}],
           [_appt("a1"), _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC))])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "appointment"
    assert len(result.clarification.candidates) == 2


async def test_appointment_override_skips_appt_resolution(monkeypatch) -> None:
    called = {"appts": False}

    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "c1", "full_name": "Ana López"}]

    async def _find_appts(*a, **k):  # type: ignore[no-untyped-def]
        called["appts"] = True
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm, appointment_override=_appt("aX"))
    assert not called["appts"]
    assert result.proposed_action["params"]["appointment_id"] == "aX"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_cancel_agent.py -q`
Expected: FAIL — `propose_cancellation` no acepta `client_override`/`appointment_override`; no devuelve `clarification`.

- [ ] **Step 3: Implement** — replace `propose_cancellation` in `cancel_agent.py` and update imports.

Add import:
```python
from app.agents.action_agent import (
    ProposalResult,
    clarify_or_abstain_appointment,
    clarify_or_abstain_client,
)
```
Replace the function:
```python
async def propose_cancellation(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return ProposalResult(None, abstained=True, message=GENERIC_MESSAGE, reason="extract_failed")

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client

    when: datetime | None = None
    if extracted.when:
        try:
            when = datetime.fromisoformat(extracted.when)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            when = None

    if appointment_override is not None:
        appt = appointment_override
    else:
        appt_res = await resolve_single_appointment(
            practice_id, client, when, now=now, limit=settings.appt_name_match_limit
        )
        if appt_res.appointment is None:
            return clarify_or_abstain_appointment(appt_res)
        appt = appt_res.appointment
    start = appt["start_at"]

    params: dict[str, Any] = {
        "appointment_id": appt["id"],
        "client_name": client["full_name"],
        "practitioner_name": appt["practitioner_full_name"],
        "start_at": start.isoformat(),
    }
    proposed_action = {
        "kind": "cancel_appointment",
        "summary": _card_summary(client["full_name"], appt["practitioner_full_name"], start),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_cancel_agent.py -q`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/cancel_agent.py backend/tests/test_cancel_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/cancel_agent.py backend/tests/test_cancel_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/cancel_agent.py backend/tests/test_cancel_agent.py
git commit -m "feat(cancel): overrides de cliente/turno + clarification para slot-filling"
```

---

### Task 7: `clarify_node` + entry routing + `_handle_proposal_result` + build (mecanismo completo)

**Files:**
- Modify: `backend/app/graph/nodes.py` (+`_numbered`, `_clarification_message`, `_handle_proposal_result`, `clarify_node`; `propose_action_node` usa el handler; `confirm_action_node` limpia `proposed_action`)
- Modify: `backend/app/graph/edges.py` (+`entry_route`, `route_after_clarify`)
- Modify: `backend/app/graph/build.py` (+nodo `clarify`, entry condicional)
- Test: `backend/tests/test_edges.py`, `backend/tests/test_nodes.py`, `backend/tests/test_slotfill_cycle.py` (crear)

**Interfaces:**
- Consumes: `resolve_choice` (Task 5), `REGISTRY[kind].propose(..., client_override=, appointment_override=)` (Task 6 para cancel), `Clarification`/`ProposalResult.clarification` (Task 4), `_format_candidate` (resolvers, existe).
- Produces:
  - `entry_route(state) -> "clarify" | "router"`.
  - `route_after_clarify = route_after_propose`.
  - `clarify_node(state) -> dict` y `_handle_proposal_result(result, *, kind, question, overrides) -> dict`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_edges.py` — add (import `entry_route` at top):
```python
from app.graph.edges import _INTENT_TO_NODE, entry_route, route_after_propose


def test_entry_route_to_clarify_when_pending() -> None:
    state = new_state("x", "p", "t")
    state["pending_clarification"] = {"kind": "cancel_appointment", "stage": "client"}
    assert entry_route(state) == "clarify"


def test_entry_route_to_router_when_no_pending() -> None:
    assert entry_route(new_state("x", "p", "t")) == "router"
```
`backend/tests/test_nodes.py` — add (helper to capture the returned patch + clarify tests). Put new imports at TOP:
```python
async def _final(node, state):
    """Corre un nodo y devuelve el AgentState final (parche aplicado)."""
    graph = _one_node_graph(node)
    return await graph.ainvoke(state)


def _appt_cand(aid="a1", dt=None):
    from datetime import UTC, datetime
    dt = dt or datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    return {"id": aid, "start_at": dt, "end_at": dt, "status": "programado",
            "practitioner_id": "p1", "practitioner_full_name": "Dra. Gómez"}


async def test_propose_action_clarification_sets_pending(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import Clarification, ProposalResult

    async def _clf(question, llm=None):
        return "cancel_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None, **kw):
        return ProposalResult(None, abstained=True, message="m", reason="appointment_ambiguous",
                              clarification=Clarification("appointment", [_appt_cand("a1"), _appt_cand("a2")], "Tiene varios turnos"))

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(write_tools.REGISTRY, "cancel_appointment", write_tools.WriteTool(
        kind="cancel_appointment", propose=_propose, write=write_tools._write_cancel,
        format_receipt=write_tools.format_cancel_receipt, cancel_message="x"))
    out = await _final(nodes.propose_action_node, new_state("cancelá", "p", "t"))
    pending = out["pending_clarification"]
    assert pending["stage"] == "appointment" and len(pending["candidates"]) == 2
    assert pending["kind"] == "cancel_appointment" and pending["overrides"] == {}


async def test_clarify_maps_and_proposes(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    action = {"kind": "cancel_appointment", "summary": "s", "params": {"appointment_id": "a1"}}

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1

    async def _propose(question, practice_id, *, now, gen_llm=None, client_override=None, appointment_override=None):
        assert appointment_override is not None  # el slot elegido se pasó como override
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(write_tools.REGISTRY, "cancel_appointment", write_tools.WriteTool(
        kind="cancel_appointment", propose=_propose, write=write_tools._write_cancel,
        format_receipt=write_tools.format_cancel_receipt, cancel_message="x"))
    state = new_state("el primero", "p", "t")
    state["pending_clarification"] = {"kind": "cancel_appointment", "stage": "appointment",
        "candidates": [_appt_cand("a1")], "question": "cancelá el turno de Ana", "overrides": {}}
    out = await _final(nodes.clarify_node, state)
    assert out["proposed_action"] == action and out["pending_clarification"] is None


async def test_clarify_no_match_clears_and_retries(monkeypatch):
    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 0  # no mapea

    called = {"propose": False}

    async def _propose(*a, **k):
        called["propose"] = True

    from app.agents import write_tools
    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(write_tools.REGISTRY, "cancel_appointment", write_tools.WriteTool(
        kind="cancel_appointment", propose=_propose, write=write_tools._write_cancel,
        format_receipt=write_tools.format_cancel_receipt, cancel_message="x"))
    state = new_state("cualquier cosa", "p", "t")
    state["pending_clarification"] = {"kind": "cancel_appointment", "stage": "client",
        "candidates": [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}],
        "question": "cancelá el turno de Ana", "overrides": {}}
    out = await _final(nodes.clarify_node, state)
    assert out["pending_clarification"] is None and not called["propose"]
    assert "No identifiqué" in out["messages"][-1].content


async def test_clarify_chains_client_then_appointment(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import Clarification, ProposalResult

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1

    async def _propose(question, practice_id, *, now, gen_llm=None, client_override=None, appointment_override=None):
        assert client_override is not None  # el cliente elegido se fijó
        return ProposalResult(None, abstained=True, message="m", reason="appointment_ambiguous",
                              clarification=Clarification("appointment", [_appt_cand("a1"), _appt_cand("a2")], "Tiene varios"))

    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(write_tools.REGISTRY, "cancel_appointment", write_tools.WriteTool(
        kind="cancel_appointment", propose=_propose, write=write_tools._write_cancel,
        format_receipt=write_tools.format_cancel_receipt, cancel_message="x"))
    state = new_state("la González", "p", "t")
    state["pending_clarification"] = {"kind": "cancel_appointment", "stage": "client",
        "candidates": [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}],
        "question": "cancelá el turno de Ana", "overrides": {}}
    out = await _final(nodes.clarify_node, state)
    pending = out["pending_clarification"]
    assert pending["stage"] == "appointment"
    assert pending["overrides"]["client"] == {"id": "1", "full_name": "Ana A"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_nodes.py -q`
Expected: FAIL — `entry_route`, `clarify_node`, `resolve_choice` (en nodes) undefined.

- [ ] **Step 3: Implement**

`backend/app/graph/nodes.py` — add imports:
```python
from app.agents.choice_agent import resolve_choice
from app.agents.resolvers import _format_candidate
```
Add helpers + `clarify_node`, and route `propose_action_node` through the handler:
```python
def _numbered(candidates: list[dict], stage: str) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        label = c["full_name"] if stage == "client" else _format_candidate(c)
        lines.append(f"{i}. {label}")
    return "\n".join(lines)


def _handle_proposal_result(result: Any, *, kind: str, question: str, overrides: dict) -> dict:
    if result.clarification is not None:
        clar = result.clarification
        pending = {"kind": kind, "stage": clar.stage, "candidates": clar.candidates,
                   "question": question, "overrides": overrides}
        msg = f"{clar.prompt}:\n{_numbered(clar.candidates, clar.stage)}\n¿Cuál? Respondé con el número."
        write_token(msg)
        write_sources([])
        return {"pending_clarification": pending, "proposed_action": None, "sources": [],
                "messages": [AIMessage(content=msg)]}
    if result.abstained:
        write_token(result.message)
        write_sources([])
        return {"pending_clarification": None, "proposed_action": None, "sources": [],
                "messages": [AIMessage(content=result.message)]}
    return {"pending_clarification": None, "proposed_action": result.proposed_action}
```
In `propose_action_node`, the `kind not in REGISTRY` branch return adds `"pending_clarification": None,`; the final two lines become:
```python
    result = await REGISTRY[kind].propose(question, state["practice_id"], now=datetime.now(UTC))
    return _handle_proposal_result(result, kind=kind, question=question, overrides={})
```
Add `clarify_node`:
```python
async def clarify_node(state: AgentState) -> dict:
    pending = state["pending_clarification"]
    assert pending is not None  # entry_route garantiza no-None acá
    candidates = pending["candidates"]
    reply = last_user_text(state)
    idx = await resolve_choice(_numbered(candidates, pending["stage"]), reply, n=len(candidates))
    if not 1 <= idx <= len(candidates):
        msg = "No identifiqué cuál; volvé a pedírmelo indicando la fecha o el nombre completo."
        write_token(msg)
        write_sources([])
        return {"pending_clarification": None, "sources": [], "messages": [AIMessage(content=msg)]}
    overrides = {**pending["overrides"], pending["stage"]: candidates[idx - 1]}
    result = await REGISTRY[pending["kind"]].propose(
        pending["question"], state["practice_id"], now=datetime.now(UTC),
        client_override=overrides.get("client"), appointment_override=overrides.get("appointment"),
    )
    return _handle_proposal_result(result, kind=pending["kind"],
                                   question=pending["question"], overrides=overrides)
```
In `confirm_action_node`, change the final return to also clear the consumed proposal:
```python
    return {"sources": [], "proposed_action": None, "messages": [AIMessage(content=msg)]}
```

`backend/app/graph/edges.py` — add:
```python
def entry_route(state: AgentState) -> str:
    return "clarify" if state.get("pending_clarification") else "router"


route_after_clarify = route_after_propose
```

`backend/app/graph/build.py` — wire the node + conditional entry:
```python
from app.graph.edges import entry_route, route, route_after_propose
from app.graph.nodes import (
    chitchat_node,
    clarify_node,
    confirm_action_node,
    propose_action_node,
    rag_node,
    scope_reject_node,
    sql_node,
)
```
In `build_graph`, add the node and replace `g.add_edge(START, "router")`:
```python
    g.add_node("clarify", clarify_node)
    ...
    g.add_conditional_edges(START, entry_route, {"clarify": "clarify", "router": "router"})
    g.add_conditional_edges(
        "clarify", route_after_propose, {"confirm_action": "confirm_action", END: END}
    )
```
(keep the existing `router`/`propose_action` conditional edges and `_LEAF_NODES` loop unchanged.)

- [ ] **Step 4: Write the slot-filling integration test (MemorySaver)**

```python
# backend/tests/test_slotfill_cycle.py
from datetime import UTC, datetime

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agents import write_tools
from app.agents.action_agent import Clarification, ProposalResult
from app.graph.build import build_graph
from app.graph.state import new_state


def _appt(aid, dt):  # type: ignore[no-untyped-def]
    return {"id": aid, "start_at": dt, "end_at": dt, "status": "programado",
            "practitioner_id": "p1", "practitioner_full_name": "Dra. Gómez"}


async def test_slotfill_client_then_appointment_then_confirm(monkeypatch) -> None:
    cands_client = [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]
    cands_appt = [_appt("a1", datetime(2026, 7, 1, 14, 0, tzinfo=UTC)),
                  _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC))]
    action = {"kind": "cancel_appointment", "summary": "s",
              "params": {"appointment_id": "a1", "client_name": "Ana A",
                         "practitioner_name": "Dra. Gómez", "start_at": "2026-07-01T14:00:00+00:00"}}

    async def _clf(question, llm=None):
        return "cancel_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None, client_override=None, appointment_override=None):
        if client_override is None:
            return ProposalResult(None, abstained=True, message="m", reason="client_ambiguous",
                                  clarification=Clarification("client", cands_client, "Hay varios clientes"))
        if appointment_override is None:
            return ProposalResult(None, abstained=True, message="m", reason="appointment_ambiguous",
                                  clarification=Clarification("appointment", cands_appt, "Tiene varios turnos"))
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    write_spy = {"n": 0}

    async def _write(practice_id, params):
        write_spy["n"] += 1
        return {"cancelled": True, "id": "a1", "status": "cancelado", "start_at": datetime(2026, 7, 1, 14, 0, tzinfo=UTC)}

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1  # el usuario elige siempre la opción 1

    # `nodes` importa estos nombres directamente → se parchean en app.graph.nodes
    monkeypatch.setattr("app.graph.nodes.classify_write_action", _clf)
    monkeypatch.setattr("app.graph.nodes.resolve_choice", _choice)
    monkeypatch.setitem(write_tools.REGISTRY, "cancel_appointment", write_tools.WriteTool(
        kind="cancel_appointment", propose=_propose, write=_write,
        format_receipt=lambda p, r: "✅ ok", cancel_message="x"))

    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "slotfill-1"}}

    await graph.ainvoke(new_state("cancelá el turno de Ana", "pid", "slotfill-1"), cfg)
    snap = await graph.aget_state(cfg)
    assert snap.values["pending_clarification"]["stage"] == "client"

    await graph.ainvoke({"messages": [HumanMessage(content="la A")]}, cfg)
    snap = await graph.aget_state(cfg)
    assert snap.values["pending_clarification"]["stage"] == "appointment"

    await graph.ainvoke({"messages": [HumanMessage(content="el del 1")]}, cfg)
    snap = await graph.aget_state(cfg)
    assert snap.next == ("confirm_action",)  # se abrió la tarjeta

    await graph.ainvoke(Command(resume="confirm"), cfg)
    assert write_spy["n"] == 1
```

- [ ] **Step 5: Run all graph tests + commit**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_nodes.py backend/tests/test_slotfill_cycle.py backend/tests/test_hitl_cycle.py -q`
Expected: PASS. Then full gate:
Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS.
```bash
backend\.venv\Scripts\python -m ruff format backend/app/graph/ backend/tests/test_edges.py backend/tests/test_nodes.py backend/tests/test_slotfill_cycle.py
backend\.venv\Scripts\python -m ruff check backend/app/graph/ backend/tests/test_edges.py backend/tests/test_nodes.py backend/tests/test_slotfill_cycle.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/graph/ backend/tests/test_edges.py backend/tests/test_nodes.py backend/tests/test_slotfill_cycle.py
git commit -m "feat(graph): clarify_node + entry condicional (slot-filling end-to-end con cancel)"
```

---

### Task 8: `reschedule_agent` — overrides + `clarification`

**Files:**
- Modify: `backend/app/agents/reschedule_agent.py`
- Test: `backend/tests/test_reschedule_agent.py`

**Interfaces:**
- Produces: `propose_reschedule(..., client_override=None, appointment_override=None)` — mismo contrato que `propose_cancellation`.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_reschedule_agent.py`, mirroring Task 6; reuse that file's existing fakes/helpers for `ProposedReschedule`)

```python
async def test_reschedule_client_override_skips_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _find_appts(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return [_appt()]  # helper local del archivo

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-05T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá el turno de Ana al 5/7 15:00", "pid", now=NOW, gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"})
    assert not called["clients"] and result.proposed_action is not None


async def test_reschedule_client_ambiguous_clarification(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}], [])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-05T15:00:00"))
    result = await reschedule_agent.propose_reschedule("reprogramá", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "client"


async def test_reschedule_appointment_ambiguous_clarification(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}],
           [_appt("a1"), _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC))])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-05T15:00:00"))
    result = await reschedule_agent.propose_reschedule("reprogramá", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "appointment"
```
> If `test_reschedule_agent.py` doesn't already define `_patch`/`_appt`/`FakeGenLLM`/`NOW`, copy them from `test_cancel_agent.py` (same shape; `_appt` returns the dict with `id/start_at/end_at/status/practitioner_*`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_reschedule_agent.py -q`
Expected: FAIL — kwargs/clarification missing.

- [ ] **Step 3: Implement** — in `reschedule_agent.py` add the import and the overrides (the new-time parsing stays between client and appointment resolution).

Import:
```python
from app.agents.action_agent import (
    ProposalResult,
    clarify_or_abstain_appointment,
    clarify_or_abstain_client,
)
```
Change the signature and the two resolution blocks (rest of the function unchanged):
```python
async def propose_reschedule(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client

    # ... (new_start parsing + new_time_past check: UNCHANGED) ...

    current_when = _parse_when(extracted.current_when)
    if appointment_override is not None:
        appt = appointment_override
    else:
        appt_res = await resolve_single_appointment(
            practice_id, client, current_when, now=now, limit=settings.appt_name_match_limit
        )
        if appt_res.appointment is None:
            return clarify_or_abstain_appointment(appt_res)
        appt = appt_res.appointment
    old_start = appt["start_at"]
    # ... (new_end, params, proposed_action: UNCHANGED) ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_reschedule_agent.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/reschedule_agent.py backend/tests/test_reschedule_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/reschedule_agent.py backend/tests/test_reschedule_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/reschedule_agent.py backend/tests/test_reschedule_agent.py
git commit -m "feat(reschedule): overrides de cliente/turno + clarification para slot-filling"
```

---

### Task 9: `interaction_agent` + `update_client_agent` — `client_override` + `clarification`

**Files:**
- Modify: `backend/app/agents/interaction_agent.py`, `backend/app/agents/update_client_agent.py`
- Test: `backend/tests/test_interaction_agent.py`, `backend/tests/test_update_client_agent.py`

**Interfaces:**
- Produces: `propose_interaction(..., client_override=None, appointment_override=None)` y `propose_update_client(..., client_override=None, appointment_override=None)` — aceptan ambos overrides por uniformidad del dispatch; usan solo `client_override`. Ante cliente ambiguo → `clarification` stage `client`.

> **Why `appointment_override` here too:** `clarify_node` llama a `REGISTRY[kind].propose(..., client_override=, appointment_override=)` para CUALQUIER kind. Estas tools no tienen turno, pero deben **aceptar** el kwarg (lo ignoran) o la llamada con keyword falla con `TypeError`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_interaction_agent.py` — add (reuse the file's existing fakes; `ProposedInteraction` needs `summary`/`content`):
```python
async def test_interaction_client_override_skips_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", type="nota", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá una nota de Ana", "pid", now=NOW, gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"})
    assert not called["clients"] and result.proposed_action is not None


async def test_interaction_client_ambiguous_clarification(monkeypatch) -> None:
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", type="nota", summary="s", content="c"))
    result = await interaction_agent.propose_interaction("registrá una nota de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "client"
    assert result.clarification.candidates[0]["id"] == "1"
```
`backend/tests/test_update_client_agent.py` — add the analogous two tests (`ProposedClientUpdate(client_name="Ana", phone="11-2233-4455")`; for the override test, `db.get_client` must also be patched to return `{}`):
```python
async def test_update_client_override_skips_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _get_client(practice_id, cid):  # type: ignore[no-untyped-def]
        return {}

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "get_client", _get_client)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="11-2233-4455"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana", "pid", now=NOW, gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"})
    assert not called["clients"] and result.proposed_action is not None


async def test_update_client_ambiguous_clarification(monkeypatch) -> None:
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="11-2233-4455"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "client"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py backend/tests/test_update_client_agent.py -q`
Expected: FAIL — kwargs/clarification missing.

- [ ] **Step 3: Implement** (both files)

`interaction_agent.py` — add import:
```python
from app.agents.action_agent import ProposalResult, clarify_or_abstain_client
```
Change signature and client block:
```python
async def propose_interaction(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,  # ignorado; uniformidad del dispatch
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, gen_llm)
    if extracted is None:
        return ProposalResult(
            proposed_action=None, abstained=True, message=GENERIC_MESSAGE, reason="extract_failed"
        )

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client
    # ... (params / proposed_action: UNCHANGED) ...
```
`update_client_agent.py` — same pattern: add `from app.agents.action_agent import ProposalResult, clarify_or_abstain_client`, add the two override kwargs to the signature, and replace the `resolution = await resolve_single_client(...) / if resolution.client is None: return _abstain(...)` block with the `client_override`/`clarify_or_abstain_client` block above (the rest — `changes` collection, `db.get_client`, `proposed_action` — unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py backend/tests/test_update_client_agent.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/interaction_agent.py backend/app/agents/update_client_agent.py backend/tests/test_interaction_agent.py backend/tests/test_update_client_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/interaction_agent.py backend/app/agents/update_client_agent.py backend/tests/test_interaction_agent.py backend/tests/test_update_client_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/interaction_agent.py backend/app/agents/update_client_agent.py backend/tests/test_interaction_agent.py backend/tests/test_update_client_agent.py
git commit -m "feat(log,update_client): client_override + clarification para slot-filling de cliente"
```

---

### Task 10: unificar `propose_appointment` sobre `resolve_single_client` + `client_override` + `clarification`

**Files:**
- Modify: `backend/app/agents/action_agent.py` (`propose_appointment`)
- Test: `backend/tests/test_action_agent.py`

**Interfaces:**
- Produces: `propose_appointment(..., client_override=None, appointment_override=None)`. El resolver de **profesional** queda igual (abstención dura); solo el **cliente** pasa por `resolve_single_client` + `clarify_or_abstain_client`.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_action_agent.py`)

```python
async def test_create_client_override_skips_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _list_pracs(practice_id):  # type: ignore[no-untyped-def]
        return [{"id": "p1", "full_name": "Dra. Gómez"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "list_active_practitioners", _list_pracs)
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment(
        "agendá un turno para Ana el 5/7 10:00", "pid", now=NOW, gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"})
    assert not called["clients"] and result.proposed_action is not None
    assert result.proposed_action["params"]["client_id"] == "c1"


async def test_create_client_ambiguous_clarification(monkeypatch) -> None:
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment("agendá un turno para Ana", "pid", now=NOW, gen_llm=llm)
    assert result.clarification is not None and result.clarification.stage == "client"


async def test_create_client_not_found_still_abstains(monkeypatch) -> None:
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedAppointment(client_name="Zzz", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment("agendá un turno para Zzz", "pid", now=NOW, gen_llm=llm)
    assert result.abstained and result.reason == "client_not_found" and result.clarification is None
```
> The file already provides `FakeGenLLM`/`ProposedAppointment`/`NOW` (it tests `propose_appointment`); reuse them. If `db.find_clients_by_name` wasn't imported/used before, the monkeypatch still works (it's the function `resolve_single_client` calls).

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_agent.py -q`
Expected: FAIL — `client_override` kwarg / `clarification` missing; create still uses its inline resolver.

- [ ] **Step 3: Implement** — in `action_agent.py`, add `resolve_single_client` to the resolvers import, add the kwargs, and replace the inline client block (current lines ~88-109: the `if not extracted.client_name.strip()` + `clients = await db.find_clients_by_name(...)` + not-found/ambiguous/`client = clients[0]`) with:
```python
async def propose_appointment(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,  # ignorado; uniformidad del dispatch
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client

    # ... (practitioner resolution + datetime parse + params + proposed_action: UNCHANGED) ...
```
Update import:
```python
from app.agents.resolvers import resolve_single_client
```
(`clarify_or_abstain_client` is defined in this same module — no import needed.)

- [ ] **Step 4: Run tests + full gate**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_agent.py backend/tests/test_create_appointment.py -q`
Expected: PASS (incl. existing create tests — the empty-name case now yields `client_missing` via `resolve_single_client`; if an existing test asserted the old `client_missing`/`No me dijiste...` message text, update that assert to the resolver's message `"¿Sobre qué cliente es? Decime el nombre."`).
Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/action_agent.py backend/tests/test_action_agent.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/action_agent.py backend/tests/test_action_agent.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/agents/action_agent.py backend/tests/test_action_agent.py
git commit -m "feat(create): unifica el resolver de cliente sobre resolve_single_client + slot-filling"
```

---

### Task 11: e2e `-m llm` + smoke

**Files:**
- Create: `backend/tests/test_short_term_memory_e2e_llm.py`

**Interfaces:**
- Consumes: `build_graph(checkpointer=MemorySaver())`, `seed_demo()`, `db.*`, the real graph end-to-end (requires Ollama + Postgres).

- [ ] **Step 1: Write the e2e tests** (real models; mirror `test_cancel_e2e_llm.py` for seeding/cleanup)

```python
# backend/tests/test_short_term_memory_e2e_llm.py
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _status(appt_id: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt_id)


async def _seed_client_two_appts(pid: str) -> tuple[str, str, list[str]]:
    """Cliente único raro con DOS turnos futuros → ambigüedad de TURNO determinística."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testmemo " + uuid4().hex[:6]
    client_id = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text", pid, full_name)
    ids = []
    try:
        for d in (3, 6):  # lunes/jueves ficticios: dos días distintos
            start = datetime.now(UTC) + timedelta(days=d)
            a = await db.create_appointment(pid, client_id, prac["id"], start, start + timedelta(minutes=30))
            ids.append(a["id"])
    except Exception:
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
        raise
    return full_name, client_id, ids


@pytest.mark.llm
@pytest.mark.integration
async def test_slotfill_appointment_disambiguation_cancels_chosen() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_ids = await _seed_client_two_appts(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "e2e-memo-appt"}}
        # Turno 1: pedido ambiguo (dos turnos) → pregunta cuál
        await graph.ainvoke(new_state(f"cancelá el turno de {full_name}", pid, "e2e-memo-appt"), cfg)
        snap = await graph.aget_state(cfg)
        assert snap.values["pending_clarification"] is not None
        assert snap.values["pending_clarification"]["stage"] == "appointment"
        # Turno 2: elijo "el primero" → abre la tarjeta
        await graph.ainvoke({"messages": [HumanMessage(content="el primero")]}, cfg)
        snap = await graph.aget_state(cfg)
        assert snap.next == ("confirm_action",)
        await graph.ainvoke(Command(resume="confirm"), cfg)
        cancelled = [a for a in appt_ids if await _status(a) == "cancelado"]
        assert len(cancelled) == 1  # se canceló exactamente UNO (el elegido)
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)


@pytest.mark.llm
@pytest.mark.integration
async def test_chitchat_remembers_within_thread() -> None:
    pid = get_settings().practice_id
    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "e2e-memo-chat"}}
    await graph.ainvoke(new_state("hola, mi profesional de referencia es la Dra. Gómez", pid, "e2e-memo-chat"), cfg)
    out = await graph.ainvoke({"messages": [HumanMessage(content="¿quién dije que es mi profesional?")]}, cfg)
    last = out["messages"][-1].content
    assert "Gómez" in last or "Gomez" in last


@pytest.mark.llm
@pytest.mark.integration
async def test_no_match_clears_pending() -> None:
    pid = get_settings().practice_id
    full_name, client_id, _ = await _seed_client_two_appts(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "e2e-memo-nomatch"}}
        await graph.ainvoke(new_state(f"cancelá el turno de {full_name}", pid, "e2e-memo-nomatch"), cfg)
        snap = await graph.aget_state(cfg)
        assert snap.values["pending_clarification"] is not None
        # respuesta que no identifica candidato → limpia el pending
        await graph.ainvoke({"messages": [HumanMessage(content="mejor mostrame otra cosa")]}, cfg)
        snap = await graph.aget_state(cfg)
        assert snap.values["pending_clarification"] is None
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
```
> **Flakiness note (gotcha):** Ollama hiccups under load can make `resolve_choice`/extraction fail-closed (→ no-mapea / abstención). If `test_no_match_clears_pending` or the disambiguation test jitters, retry; do NOT weaken asserts. The asserts here are non-vacuous (they check the actual pending/cancelled state).

- [ ] **Step 2: Run the e2e suite** (Ollama up + `docker compose up -d` + schema/seed)

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_short_term_memory_e2e_llm.py -m llm -q`
Expected: PASS (3 passed). Then the full `-m llm` no-regression:
Run: `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q`
Expected: PASS (existing e2e of cancel/create/log/reschedule/update + the 3 new).

- [ ] **Step 3: Manual smoke (CLAUDE.md §2)** — `docker compose up -d`; backend `backend\.venv\Scripts\python backend\dev.py`; `npm --prefix frontend run dev`; seed with `backend\.venv\Scripts\python backend\seed_demo.py`. In the browser, in ONE conversation:
  - *"cancelá el turno de \<cliente con varios turnos\>"* → lista numerada «¿Cuál?» → *"el primero"* → **tarjeta** → Confirmar → ✅ y la fila queda `cancelado` en la DB.
  - *"hola, soy \<algo\>"* → *"¿qué te dije recién?"* → recuerda.
  - Con una aclaración abierta, tipear algo no relacionado → «No identifiqué cuál…» y el turno siguiente rutea normal.
  - One-shot de las 5 tools (cliente/turno únicos) → tarjeta directa, sin preguntar (no-regresión).

- [ ] **Step 4: Commit**

```bash
backend\.venv\Scripts\python -m ruff format backend/tests/test_short_term_memory_e2e_llm.py
backend\.venv\Scripts\python -m ruff check backend/tests/test_short_term_memory_e2e_llm.py
git add backend/tests/test_short_term_memory_e2e_llm.py
git commit -m "test(llm): e2e de memoria corto plazo + slot-filling (cliente/turno, chitchat, no-mapea)"
```

---

## Notas de cierre (para el integrador)

- **Orden de merge:** las Tasks 1–7 ya dejan el slot-filling de **turno** funcionando end-to-end (checkpoint natural si se decide partir 8a/8b). Las Tasks 8–10 extienden a las otras tools y al cliente; la 11 valida con modelos reales.
- **No-regresión clave:** correr el gate `-m "not llm"` completo tras las Tasks 4, 7 y 10 (cambian estado/grafo/firmas compartidas).
- **Fast-follow heredado que NO se toca acá:** normalizar `when`/`new_start_at` con `astimezone(UTC)`; consolidar `_FIELD_LABELS`; `appt_resolve_limit` dedicado; denylist SQL; audit log/`consents`. Siguen fichados.
- **DoD (CLAUDE.md §6):** ruff + mypy (`--config-file`) + `pytest -m "not llm"` verdes; `-m llm` verde; smoke §2 OK; escrituras siempre tras confirmación; commits limpios.
```
