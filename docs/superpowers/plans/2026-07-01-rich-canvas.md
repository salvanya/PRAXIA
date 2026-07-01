# Canvas más rico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renderizar inline en el chat tres artefactos estructurados —tabla SQL real, citas RAG y ConfirmCards por-kind— con base de estilos Tailwind, cerrando el MVP de Fase 1.

**Architecture:** Cada artefacto es un content-part `tool-call` que el runtime adapter de assistant-ui emite, renderizado por un Tool UI registrado (`makeAssistantToolUI` + `<Thread tools={[...]}>`). Un reducer PURO traduce eventos SSE → content-parts. El backend gana un evento SSE `table` (las filas de NL2SQL ya existen; hoy se descartan). Sin cambios en HITL/RAG/router/`propose_*`.

**Tech Stack:** Next.js 15 + React 19 + TypeScript, `@assistant-ui/react@0.7.91`, Tailwind CSS v3, Vitest + Testing Library (frontend). FastAPI + LangGraph + pytest (backend).

## Global Constraints

- **Local-first / $0:** cero red saliente NUEVA del producto en runtime. Tailwind/PostCSS son tooling de build (dev-time). (CLAUDE.md §0)
- **Tailwind v3, NO v4.** El preset de a-ui (`@assistant-ui/react/tailwindcss`) usa `tailwindcss@^3.4.17`. v4 tiene config incompatible.
- **assistant-ui fijo en 0.7.91.** Artefactos como content-parts `tool-call` (campos: `type:"tool-call"`, `toolCallId`, `toolName`, `args`, `argsText` OBLIGATORIO, `result?`). `UIContentPart` está DEPRECADO (se va en 0.8.0) → no usarlo.
- **HITL airtight:** toda escritura sigue abriendo tarjeta de confirmación; el backend de confirmación (`/chat/resume` + interrupt) NO cambia.
- **Serialización del evento `table`:** `json.dumps(..., ensure_ascii=False, default=str)` — `run_select` devuelve datetime/Decimal/date/UUID no-JSON-nativos.
- **Commits LIMPIOS:** sin ninguna atribución a Claude (sin trailer `Co-Authored-By`). Autor = usuario. (CLAUDE.md §6)
- **Backend gates:** `ruff format` ANTES de `ruff check`; `mypy --config-file backend/pyproject.toml`; `pytest -m "not llm"` no regresiona. Imports nuevos en archivos de test EXISTENTES van al TOP (ruff E402) o son imports locales dentro de la función.
- **Frontend gates:** `npm --prefix frontend run test -- --run`, `... run lint`, `... run build`.
- **Windows:** backend se arranca con `backend\.venv\Scripts\python backend\dev.py` (NO uvicorn directo). Si el hot-reload se cuelga tras muchos edits, matar el reloader (`taskkill //F //T //PID <pid de netstat :8000>`) y relanzar.
- **Firma de helpers backend de test:** `new_state(message, practice_id, thread_id)`; en `test_nodes.py` existen `_one_node_graph(node)`, `_run(node, state) -> (tokens, sources)`, `_final(node, state) -> state`. En `test_sse_stream.py` existe `_FakeGraph(items)`.

---

## File Structure

**Frontend (crear):**
- `frontend/tailwind.config.ts` — config Tailwind + preset a-ui.
- `frontend/postcss.config.mjs` — PostCSS (tailwindcss + autoprefixer).
- `frontend/lib/messageParts.ts` — reducer PURO `evento SSE → content-parts` (+ `toContent`).
- `frontend/lib/messageParts.test.ts` — tests del reducer.
- `frontend/components/Citations.tsx` — footnotes de citas RAG.
- `frontend/components/Citations.test.tsx`
- `frontend/components/SqlTable.tsx` — tabla SQL read-only.
- `frontend/components/SqlTable.test.tsx`
- `frontend/components/toolUIs.tsx` — `makeAssistantToolUI` para los 3 toolNames.

**Frontend (modificar):**
- `frontend/package.json` — devDeps Tailwind.
- `frontend/app/globals.css` — directivas `@tailwind`.
- `frontend/app/layout.tsx` — quitar import de CSS prearmado (lo reemplaza el plugin).
- `frontend/app/page.tsx` — `tools={[...]}`, quitar `pending`/`onConfirm`, i18n, clases Tailwind.
- `frontend/lib/chatStream.ts` — evento `table`.
- `frontend/lib/chatStream.test.ts` — test del evento `table`.
- `frontend/lib/runtime.ts` — usar el reducer; quitar `sourcesBlock`/`onConfirm`.
- `frontend/components/ConfirmCard.tsx` — refactor por-kind.
- `frontend/components/ConfirmCard.test.tsx` — reescribir para per-kind.

**Backend (modificar):**
- `backend/app/agents/sql_present.py` — prosa sin tabla markdown.
- `backend/tests/test_sql_present.py` — actualizar tests.
- `backend/app/graph/nodes.py` — `write_table` + emisión en `sql_node`.
- `backend/tests/test_nodes.py` — tests de emisión de `table`.
- `backend/app/main.py` — reenvío del evento `table`.
- `backend/tests/test_sse_stream.py` — test del reenvío.

**Docs (modificar):**
- `frontend/SMOKE.md` — checks nuevos.

---

## Task 1: Tailwind + assistant-ui setup

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.mjs`
- Modify: `frontend/app/globals.css`
- Modify: `frontend/app/layout.tsx`

**Interfaces:**
- Produces: clases utilitarias de Tailwind disponibles en `app/` y `components/`; el `<Thread>` de a-ui sigue estilado.

- [ ] **Step 1: Instalar Tailwind v3 + PostCSS (dev-deps)**

Run:
```bash
npm --prefix frontend install -D tailwindcss@^3.4.17 postcss@^8.5.1 autoprefixer@^10.4.20
```
Expected: se agregan a `devDependencies` en `frontend/package.json`. (NO instalar tailwindcss v4.)

- [ ] **Step 2: Crear `frontend/postcss.config.mjs`**

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 3: Crear `frontend/tailwind.config.ts`**

```ts
import type { Config } from "tailwindcss";
import aui from "@assistant-ui/react/tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./node_modules/@assistant-ui/react/dist/**/*.{js,mjs}",
  ],
  plugins: [aui({ components: ["default-theme"] })],
};

export default config;
```

- [ ] **Step 4: Reescribir `frontend/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  html,
  body {
    height: 100%;
    font-family: system-ui, sans-serif;
  }
}
```

- [ ] **Step 5: Quitar el import de CSS prearmado en `frontend/app/layout.tsx`**

Reemplazar el archivo completo por:
```tsx
import "./globals.css";

export const metadata = { title: "Praxia" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
```
(Se elimina `import "@assistant-ui/react/styles/index.css";` — el plugin `default-theme` genera esos estilos.)

- [ ] **Step 6: Verificar build + tests existentes**

Run:
```bash
npm --prefix frontend run build
npm --prefix frontend run test -- --run
```
Expected: `build` PASA; los tests existentes PASAN (ninguno depende de estilos).

- [ ] **Step 7: Verificación MANUAL en navegador**

Run: `npm --prefix frontend run dev` y abrir el front (`127.0.0.1`).
Expected: la app carga y el `<Thread>` se ve ESTILADO (caja de input redondeada, mensajes formateados) igual que antes.
**Fallback si el Thread se ve SIN estilo:** volver a agregar `import "@assistant-ui/react/styles/index.css";` en `layout.tsx` (debajo de `./globals.css`) y quitar `plugins: [aui(...)]` de `tailwind.config.ts` dejando `plugins: []` (Tailwind sólo para nuestros componentes; a-ui usa su CSS prearmado). Re-verificar.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/postcss.config.mjs frontend/tailwind.config.ts frontend/app/globals.css frontend/app/layout.tsx
git commit -m "feat(canvas): setup Tailwind v3 + preset assistant-ui"
```

---

## Task 2: Citas RAG inline (de-risk del mecanismo tool-call)

Primer artefacto end-to-end: prueba que `<Thread tools>` + content-parts `tool-call` renderizan inline. Introduce el reducer puro y el registro de Tool UIs. El confirm sigue por el camino viejo (`onConfirm`) hasta la Task 7 (sin regresión).

**Files:**
- Create: `frontend/lib/messageParts.ts`
- Create: `frontend/lib/messageParts.test.ts`
- Create: `frontend/components/Citations.tsx`
- Create: `frontend/components/Citations.test.tsx`
- Create: `frontend/components/toolUIs.tsx`
- Modify: `frontend/lib/runtime.ts`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Produces:
  - `reduceEvent(state: PartsState, event: ChatEvent): PartsState`
  - `toContent(state: PartsState): ThreadAssistantContentPart[]`
  - `initialPartsState: PartsState` (`{ text: string; artifacts: ArtifactPart[] }`)
  - `Citations({ sources: Source[] })`
  - `SourcesToolUI` (Tool UI para `praxia_sources`)
- Consumes: `Source`, `ChatEvent` de `lib/chatStream.ts` (existentes).

- [ ] **Step 1: Test del componente Citations — `frontend/components/Citations.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Citations } from "./Citations";

test("renders numbered sources with title and page", () => {
  render(
    <Citations
      sources={[
        { n: 1, title: "Protocolo", page: 2, document_id: "d1" },
        { n: 2, title: "Ficha", page: null, document_id: "d2" },
      ]}
    />,
  );
  expect(screen.getByText("[1]")).toBeTruthy();
  expect(screen.getByText(/Protocolo — p\.2/)).toBeTruthy();
  expect(screen.getByText("[2]")).toBeTruthy();
  // page null → sin " — p."
  expect(screen.getByText("Ficha")).toBeTruthy();
});

test("renders nothing when there are no sources", () => {
  const { container } = render(<Citations sources={[]} />);
  expect(container.firstChild).toBeNull();
});
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run components/Citations.test.tsx`
Expected: FAIL (`Citations` no existe).

- [ ] **Step 3: Implementar `frontend/components/Citations.tsx`**

```tsx
"use client";

import type { Source } from "../lib/chatStream";

export function Citations({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 border-t border-gray-200 pt-2 text-sm text-gray-600">
      <p className="mb-1 font-semibold text-gray-700">Fuentes</p>
      <ol className="space-y-0.5">
        {sources.map((s) => (
          <li key={s.n} className="flex gap-1.5">
            <span className="font-mono text-gray-400">[{s.n}]</span>
            <span>
              {s.title}
              {s.page != null ? ` — p.${s.page}` : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run components/Citations.test.tsx`
Expected: PASS.

- [ ] **Step 5: Test del reducer — `frontend/lib/messageParts.test.ts`**

```ts
import { expect, test } from "vitest";
import { initialPartsState, reduceEvent, toContent } from "./messageParts";

test("token events accumulate into text", () => {
  let s = initialPartsState;
  s = reduceEvent(s, { type: "token", text: "Hola" });
  s = reduceEvent(s, { type: "token", text: " mundo" });
  expect(s.text).toBe("Hola mundo");
  expect(s.artifacts).toEqual([]);
});

test("non-empty sources become a praxia_sources artifact", () => {
  const src = [{ n: 1, title: "P", page: 2, document_id: "d1" }];
  const s = reduceEvent(initialPartsState, { type: "sources", sources: src });
  expect(s.artifacts).toEqual([{ toolName: "praxia_sources", data: { sources: src } }]);
});

test("empty sources are ignored", () => {
  const s = reduceEvent(initialPartsState, { type: "sources", sources: [] });
  expect(s.artifacts).toEqual([]);
});

test("done/unknown events leave state unchanged", () => {
  const s = reduceEvent({ text: "x", artifacts: [] }, { type: "done" });
  expect(s).toEqual({ text: "x", artifacts: [] });
});

test("toContent puts text first, then tool-call parts with stable ids", () => {
  let s = initialPartsState;
  s = reduceEvent(s, { type: "token", text: "Según [1]" });
  s = reduceEvent(s, {
    type: "sources",
    sources: [{ n: 1, title: "P", page: 2, document_id: "d1" }],
  });
  const content = toContent(s);
  expect(content[0]).toEqual({ type: "text", text: "Según [1]" });
  expect(content[1]).toMatchObject({
    type: "tool-call",
    toolCallId: "praxia-0",
    toolName: "praxia_sources",
  });
});

test("toContent omits the text part when there is no text", () => {
  const s = reduceEvent(initialPartsState, {
    type: "sources",
    sources: [{ n: 1, title: "P", page: null, document_id: "d1" }],
  });
  const content = toContent(s);
  expect(content).toHaveLength(1);
  expect(content[0]).toMatchObject({ type: "tool-call", toolCallId: "praxia-0" });
});
```

- [ ] **Step 6: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: FAIL (`messageParts` no existe).

- [ ] **Step 7: Implementar `frontend/lib/messageParts.ts`**

```ts
import type { ThreadAssistantContentPart } from "@assistant-ui/react";
import type { ChatEvent } from "./chatStream";

export interface ArtifactPart {
  toolName: string;
  data: Record<string, unknown>;
}

export interface PartsState {
  text: string;
  artifacts: ArtifactPart[];
}

export const initialPartsState: PartsState = { text: "", artifacts: [] };

export function reduceEvent(state: PartsState, event: ChatEvent): PartsState {
  switch (event.type) {
    case "token":
      return { ...state, text: state.text + event.text };
    case "sources":
      if (!event.sources.length) return state;
      return {
        ...state,
        artifacts: [
          ...state.artifacts,
          { toolName: "praxia_sources", data: { sources: event.sources } },
        ],
      };
    default:
      // table/confirm se agregan en tasks posteriores; done/desconocidos se ignoran
      // (sin regresión: un evento sin caso deja el estado igual).
      return state;
  }
}

export function toContent(state: PartsState): ThreadAssistantContentPart[] {
  const parts: ThreadAssistantContentPart[] = [];
  if (state.text) parts.push({ type: "text", text: state.text });
  state.artifacts.forEach((a, i) => {
    // toolCallId estable por posición (los artefactos sólo crecen) → no re-monta al streamear.
    parts.push({
      type: "tool-call",
      toolCallId: `praxia-${i}`,
      toolName: a.toolName,
      args: a.data,
      argsText: JSON.stringify(a.data),
    } as ThreadAssistantContentPart);
  });
  return parts;
}
```
(Si TS objeta el `as ThreadAssistantContentPart`, usar `as unknown as ThreadAssistantContentPart`: los valores son JSON por construcción.)

- [ ] **Step 8: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: PASS.

- [ ] **Step 9: Implementar `frontend/components/toolUIs.tsx`**

```tsx
"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import type { Source } from "../lib/chatStream";
import { Citations } from "./Citations";

export const SourcesToolUI = makeAssistantToolUI<{ sources: Source[] }, unknown>({
  toolName: "praxia_sources",
  render: ({ args }) => <Citations sources={args.sources} />,
});
```

- [ ] **Step 10: Reescribir `frontend/lib/runtime.ts` (usar el reducer; citas por tool-call)**

```tsx
"use client";

import { useMemo, useRef } from "react";
import {
  useLocalRuntime,
  type ChatModelAdapter,
  type ChatModelRunOptions,
  type ChatModelRunResult,
  type ThreadUserMessage,
} from "@assistant-ui/react";
import { streamChat, type ProposedAction } from "./chatStream";
import { initialPartsState, reduceEvent, toContent, type PartsState } from "./messageParts";

export interface PendingAction {
  threadId: string;
  action: ProposedAction;
}

function lastUserText(messages: ChatModelRunOptions["messages"]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const userMsg = msg as ThreadUserMessage;
      return userMsg.content.map((p) => (p.type === "text" ? p.text : "")).join("");
    }
  }
  return "";
}

export function useChatRuntime(onConfirm?: (p: PendingAction) => void) {
  const threadIdRef = useRef<string | undefined>(undefined);
  if (!threadIdRef.current) threadIdRef.current = crypto.randomUUID();
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({
        messages,
        abortSignal,
      }: ChatModelRunOptions): AsyncGenerator<ChatModelRunResult, void> {
        const query = lastUserText(messages);
        let state: PartsState = initialPartsState;
        try {
          for await (const ev of streamChat(query, threadIdRef.current!, abortSignal)) {
            if (ev.type === "confirm") {
              // Camino viejo hasta la Task 7 (el confirm pasa a tool-call inline allí).
              onConfirm?.({ threadId: ev.threadId, action: ev.action });
              yield {
                content: [
                  { type: "text", text: "📝 Propuse una acción — revisá la tarjeta de confirmación." },
                ],
              };
              return;
            }
            state = reduceEvent(state, ev);
            yield { content: toContent(state) };
          }
        } catch (err) {
          if (abortSignal?.aborted) return;
          const message = err instanceof Error ? err.message : "No se pudo contactar al asistente.";
          yield { content: [{ type: "text", text: message }] };
          return;
        }
        yield { content: toContent(state), status: { type: "complete", reason: "stop" } };
      },
    }),
    [onConfirm],
  );
  return useLocalRuntime(adapter);
}
```
(Se elimina la función `sourcesBlock` y el `Source` import ya no hace falta acá.)

- [ ] **Step 11: Registrar el Tool UI en `frontend/app/page.tsx`**

Agregar el import y pasar `tools` al `<Thread>` (el resto de `page.tsx` queda igual por ahora):
```tsx
import { SourcesToolUI } from "../components/toolUIs";
```
```tsx
          <div style={{ flex: 1, minHeight: 0 }}>
            <Thread tools={[SourcesToolUI]} />
          </div>
```

- [ ] **Step 12: Correr toda la suite del front + lint + build**

Run:
```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```
Expected: todo PASA.

- [ ] **Step 13: Verificación MANUAL (de-risk del mecanismo)**

Con Ollama + docker + backend (`backend\.venv\Scripts\python backend\dev.py`) + front en dev: hacer una consulta documental (RAG), p. ej. "¿cuánto dura la primera consulta?".
Expected: la respuesta aparece y DEBAJO se ven las **Fuentes** numeradas renderizadas por `<Citations>` (no markdown crudo). Esto confirma que `<Thread tools>` + content-parts `tool-call` funcionan.
**Si NO renderiza** (el mensaje aparece sin citas o con un placeholder de tool): ver §Riesgos del spec — probar (a) agregar `result: a.data` además de `args` en `toContent`, o (b) `assistantMessage={{ components: { ToolFallback: ... } }}`. Ajustar y re-verificar ANTES de seguir.

- [ ] **Step 14: Commit**

```bash
git add frontend/lib/messageParts.ts frontend/lib/messageParts.test.ts frontend/components/Citations.tsx frontend/components/Citations.test.tsx frontend/components/toolUIs.tsx frontend/lib/runtime.ts frontend/app/page.tsx
git commit -m "feat(canvas): citas RAG inline como tool-call content-parts"
```

---

## Task 3: Backend — prosa SQL sin tabla markdown

**Files:**
- Modify: `backend/app/agents/sql_present.py`
- Modify: `backend/tests/test_sql_present.py`

**Interfaces:**
- Produces: `synthesize_sql_answer(...)` devuelve una frase (nunca una tabla markdown); `render_rows_markdown` se mantiene como serializador interno para el prompt del LLM.

- [ ] **Step 1: Actualizar/agregar tests en `backend/tests/test_sql_present.py`**

Reemplazar `test_render_rows_markdown_builds_table` NO (se mantiene: `render_rows_markdown` sigue existiendo). Agregar estos tests y ADAPTAR el nombre del helper `_deterministic` si se testea. Añadir al final del archivo:
```python
def test_deterministic_tabular_returns_sentence_not_markdown() -> None:
    out = sql_present._deterministic(
        [{"full_name": "Ana"}, {"full_name": "Beto"}], ["full_name"]
    )
    assert out == "Encontré 2 resultado(s)."
    assert "|" not in out


def test_deterministic_scalar_keeps_resultado_prefix() -> None:
    out = sql_present._deterministic([{"total": 12}], ["total"])
    assert out == "Resultado: 12"


async def test_synth_falls_back_when_llm_emits_markdown_table() -> None:
    # Aunque el prompt lo prohíbe, si el LLM devuelve una tabla markdown, se descarta
    # (la tabla va como artefacto estructurado, no en la prosa).
    out = await sql_present.synthesize_sql_answer(
        "listá los clientes",
        [{"full_name": "Ana"}, {"full_name": "Beto"}],
        ["full_name"],
        llm=FakeLLM("| full_name |\n| --- |\n| Ana |\n| Beto |"),
    )
    assert out == "Encontré 2 resultado(s)."
    assert "|" not in out
```

- [ ] **Step 2: Correr los tests (deben fallar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_present.py -q`
Expected: FAIL (`_deterministic` tabular aún devuelve markdown; el guard de tabla no existe).

- [ ] **Step 3: Modificar `backend/app/agents/sql_present.py`**

Cambiar `SYNTH_SYSTEM` (agregar la prohibición de tablas):
```python
SYNTH_SYSTEM = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con los datos "
    "provistos. No inventes ni calcules números nuevos. NO incluyas una tabla en tu respuesta: "
    "la tabla se muestra por separado. Resumí en UNA sola frase breve. Sé conciso."
)
```
Reemplazar `_deterministic`:
```python
def _deterministic(rows: list[dict], columns: list[str]) -> str:
    cols = columns or list(rows[0].keys())
    if len(rows) == 1 and len(cols) == 1:
        return f"Resultado: {_fmt(list(rows[0].values())[0])}"
    return f"Encontré {len(rows)} resultado(s)."
```
Agregar el guard de tabla markdown (después de `_grounded`):
```python
def _has_md_table(text: str) -> bool:
    return any(line.lstrip().startswith("|") for line in text.splitlines())
```
En `synthesize_sql_answer`, ampliar la condición de fallback:
```python
    if not answer or not _grounded(answer, rows) or _has_md_table(answer):
        return _deterministic(rows, columns)
    return answer
```

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_present.py -q`
Expected: PASS (incluidos los tests preexistentes: scalar verbatim, empty, guard de alucinación, render_rows_markdown).

- [ ] **Step 5: Lint + typecheck**

Run:
```bash
backend\.venv\Scripts\python -m ruff format backend/app/agents/sql_present.py backend/tests/test_sql_present.py
backend\.venv\Scripts\python -m ruff check backend/app/agents/sql_present.py backend/tests/test_sql_present.py
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
```
Expected: limpio.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/sql_present.py backend/tests/test_sql_present.py
git commit -m "feat(canvas): prosa SQL sin tabla markdown (la tabla va estructurada)"
```

---

## Task 4: Backend — evento SSE `table`

**Files:**
- Modify: `backend/app/graph/nodes.py`
- Modify: `backend/tests/test_nodes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_sse_stream.py`

**Interfaces:**
- Produces:
  - `nodes.write_table(columns: list[str], rows: list[dict], sql: str) -> None` (emite chunk `{"kind":"table", ...}`).
  - `sql_node` emite `table` sólo en caso TABULAR (`rows` y no escalar 1×1).
  - `main._sse_event_stream` reenvía `kind:"table"` como `event: table` con `default=str`.

- [ ] **Step 1: Test de emisión en `backend/tests/test_nodes.py`**

Agregar al final del archivo (usa `_one_node_graph` y `new_state` ya existentes; import local para evitar E402):
```python
async def _run_tables(node, state):
    """Corre un nodo y devuelve la lista de chunks 'table' emitidos."""
    graph = _one_node_graph(node)
    tables = []
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "table":
            tables.append(chunk)
    return tables


async def test_sql_node_emits_table_for_tabular_result(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(
            sql="SELECT full_name FROM clients",
            rows=[{"full_name": "Ana"}, {"full_name": "Beto"}],
            columns=["full_name"],
        )

    async def _fake_synth(question, rows, columns, llm=None):
        return "Encontré 2 resultado(s)."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tables = await _run_tables(nodes.sql_node, new_state("listá clientes", "p", "t"))
    assert len(tables) == 1
    assert tables[0]["columns"] == ["full_name"]
    assert tables[0]["rows"] == [{"full_name": "Ana"}, {"full_name": "Beto"}]
    assert tables[0]["sql"] == "SELECT full_name FROM clients"


async def test_sql_node_no_table_for_scalar(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql="SELECT count(*)", rows=[{"total": 12}], columns=["total"])

    async def _fake_synth(question, rows, columns, llm=None):
        return "Tenés 12 turnos."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tables = await _run_tables(nodes.sql_node, new_state("¿cuántos turnos?", "p", "t"))
    assert tables == []


async def test_sql_node_no_table_when_abstained(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql=None, abstained=True, reason="x")

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    tables = await _run_tables(nodes.sql_node, new_state("algo raro", "p", "t"))
    assert tables == []
```

- [ ] **Step 2: Correr los tests (deben fallar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py -q -k "table"`
Expected: FAIL (`write_table`/emisión no existen).

- [ ] **Step 3: Modificar `backend/app/graph/nodes.py`**

Agregar el helper junto a `write_sources`:
```python
def write_table(columns: list[str], rows: list[dict], sql: str) -> None:
    get_stream_writer()({"kind": "table", "columns": columns, "rows": rows, "sql": sql})
```
Reemplazar `sql_node` por:
```python
async def sql_node(state: AgentState) -> dict:
    result = await answer_structured(last_user_text(state), state["practice_id"])
    if result.abstained:
        write_token(SQL_ABSTAIN_MESSAGE)
        write_sources([])
        answer = SQL_ABSTAIN_MESSAGE
    else:
        answer = await synthesize_sql_answer(last_user_text(state), result.rows, result.columns)
        for piece in _stream_chunks(answer):
            write_token(piece)
        is_tabular = bool(result.rows) and not (
            len(result.rows) == 1 and len(result.columns) == 1
        )
        if is_tabular:
            write_table(result.columns, result.rows, result.sql or "")
        write_sources([])
    return {
        "sources": [],
        "candidate_sql": result.sql or "",
        "judge_scores": {"sql_match": not result.abstained},
        "messages": [AIMessage(content=answer)],
    }
```

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py -q`
Expected: PASS (incluye el preexistente `test_sql_node_emits_synthesized_answer`).

- [ ] **Step 5: Test del reenvío en `backend/tests/test_sse_stream.py`**

Agregar al final:
```python
async def test_stream_forwards_table_event_with_json_safe_serialization() -> None:
    from datetime import UTC, datetime

    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "kind": "table",
                    "columns": ["cliente", "fecha"],
                    "rows": [{"cliente": "Ana", "fecha": datetime(2026, 7, 10, 10, 0, tzinfo=UTC)}],
                    "sql": "SELECT cliente, fecha FROM turnos",
                },
            ),
        ]
    )
    config = {"configurable": {"thread_id": "t1"}}
    events = [e async for e in _sse_event_stream(graph, None, config)]

    table = next(e for e in events if e["event"] == "table")
    payload = json.loads(table["data"])
    assert payload["columns"] == ["cliente", "fecha"]
    assert payload["sql"] == "SELECT cliente, fecha FROM turnos"
    # datetime → string vía default=str (no explota json.dumps)
    assert payload["rows"][0]["cliente"] == "Ana"
    assert isinstance(payload["rows"][0]["fecha"], str)
    assert "2026-07-10" in payload["rows"][0]["fecha"]
```

- [ ] **Step 6: Correr el test (debe fallar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sse_stream.py -q`
Expected: FAIL (main no reenvía `table`; el evento no aparece).

- [ ] **Step 7: Modificar `backend/app/main.py`**

En `_sse_event_stream`, dentro del bloque `if mode == "custom":`, agregar tras el `elif kind == "sources":`:
```python
            elif kind == "table":
                yield {
                    "event": "table",
                    "data": json.dumps(
                        {"columns": chunk["columns"], "rows": chunk["rows"], "sql": chunk["sql"]},
                        ensure_ascii=False,
                        default=str,
                    ),
                }
```

- [ ] **Step 8: Correr el test (debe pasar)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sse_stream.py -q`
Expected: PASS (incluye el preexistente `test_stream_translates_token_sources_confirm_done`).

- [ ] **Step 9: Lint + typecheck + gate no-llm completo**

Run:
```bash
backend\.venv\Scripts\python -m ruff format backend/app/graph/nodes.py backend/app/main.py backend/tests/test_nodes.py backend/tests/test_sse_stream.py
backend\.venv\Scripts\python -m ruff check backend/app backend/tests
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q
```
Expected: limpio; gate no-llm no regresiona (≥ 264 + los nuevos).

- [ ] **Step 10: Commit**

```bash
git add backend/app/graph/nodes.py backend/app/main.py backend/tests/test_nodes.py backend/tests/test_sse_stream.py
git commit -m "feat(canvas): evento SSE table con filas SQL estructuradas"
```

---

## Task 5: Frontend — parseo del evento `table`

**Files:**
- Modify: `frontend/lib/chatStream.ts`
- Modify: `frontend/lib/chatStream.test.ts`

**Interfaces:**
- Produces: `ChatEvent` gana `{ type: "table"; table: SqlTablePayload }` con `SqlTablePayload = { columns: string[]; rows: Record<string, unknown>[]; sql: string }`.

- [ ] **Step 1: Test en `frontend/lib/chatStream.test.ts`**

Agregar (usa el helper `sseResponse` ya definido en el archivo):
```ts
test("streamChat parses a table event into a structured payload", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
    'event: table\ndata: {"columns":["cliente"],"rows":[{"cliente":"Ana"}],"sql":"SELECT cliente FROM t"}\n\n',
    "event: done\ndata: [DONE]\n\n",
  ])));

  const events = [];
  for await (const ev of streamChat("listá", "t1")) events.push(ev);

  expect(events).toEqual([
    { type: "table", table: { columns: ["cliente"], rows: [{ cliente: "Ana" }], sql: "SELECT cliente FROM t" } },
    { type: "done" },
  ]);
});
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run lib/chatStream.test.ts`
Expected: FAIL (el evento `table` no se parsea → no aparece en `events`).

- [ ] **Step 3: Modificar `frontend/lib/chatStream.ts`**

Agregar la interfaz y el tipo al union:
```ts
export interface SqlTablePayload {
  columns: string[];
  rows: Record<string, unknown>[];
  sql: string;
}
```
Extender `ChatEvent`:
```ts
export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "table"; table: SqlTablePayload }
  | { type: "confirm"; threadId: string; action: ProposedAction }
  | { type: "done" };
```
En `parseEvent`, antes del `if (event === "confirm")`:
```ts
  if (event === "table") return { type: "table", table: JSON.parse(data) as SqlTablePayload };
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run lib/chatStream.test.ts`
Expected: PASS (los tests preexistentes de token/sources/confirm siguen pasando).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/chatStream.ts frontend/lib/chatStream.test.ts
git commit -m "feat(canvas): parseo del evento SSE table en el cliente"
```

---

## Task 6: SqlTable inline

**Files:**
- Create: `frontend/components/SqlTable.tsx`
- Create: `frontend/components/SqlTable.test.tsx`
- Modify: `frontend/lib/messageParts.ts`
- Modify: `frontend/lib/messageParts.test.ts`
- Modify: `frontend/components/toolUIs.tsx`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `SqlTablePayload` (Task 5), `reduceEvent` (Task 2).
- Produces: `SqlTable({ columns, rows, sql? })`; `SqlTableToolUI` (`praxia_sql_table`); `reduceEvent` maneja `type:"table"`.

- [ ] **Step 1: Test del componente — `frontend/components/SqlTable.test.tsx`**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { SqlTable } from "./SqlTable";

test("renders columns in order and cell values", () => {
  render(
    <SqlTable
      columns={["cliente", "fecha"]}
      rows={[
        { cliente: "Ana", fecha: "10/07" },
        { cliente: "Beto", fecha: "11/07" },
      ]}
    />,
  );
  expect(screen.getByText("cliente")).toBeTruthy();
  expect(screen.getByText("fecha")).toBeTruthy();
  expect(screen.getByText("Ana")).toBeTruthy();
  expect(screen.getByText("Beto")).toBeTruthy();
});

test("shows an empty state when there are no rows", () => {
  render(<SqlTable columns={["cliente"]} rows={[]} />);
  expect(screen.getByText(/Sin resultados/)).toBeTruthy();
});

test("toggles the SQL view", () => {
  render(<SqlTable columns={["c"]} rows={[{ c: "x" }]} sql="SELECT c FROM t" />);
  expect(screen.queryByText("SELECT c FROM t")).toBeNull();
  fireEvent.click(screen.getByText(/ver consulta/));
  expect(screen.getByText("SELECT c FROM t")).toBeTruthy();
});
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run components/SqlTable.test.tsx`
Expected: FAIL (`SqlTable` no existe).

- [ ] **Step 3: Implementar `frontend/components/SqlTable.tsx`**

```tsx
"use client";

import { useState } from "react";

function fmt(v: unknown): string {
  return v == null ? "" : String(v);
}

export function SqlTable({
  columns,
  rows,
  sql,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  sql?: string;
}) {
  const [showSql, setShowSql] = useState(false);
  if (!rows.length) return <p className="my-2 text-sm text-gray-500">Sin resultados.</p>;
  const cols = columns.length ? columns : Object.keys(rows[0]);
  return (
    <div className="my-2">
      <div className="max-h-80 overflow-auto rounded-md border border-gray-200">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-gray-50">
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  className="border-b border-gray-200 px-3 py-1.5 text-left font-semibold text-gray-700"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={i % 2 ? "bg-gray-50/60" : undefined}>
                {cols.map((c) => (
                  <td key={c} className="border-b border-gray-100 px-3 py-1.5 text-gray-800">
                    {fmt(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sql ? (
        <div className="mt-1 text-xs">
          <button className="text-gray-500 underline" onClick={() => setShowSql((v) => !v)}>
            {showSql ? "ocultar consulta" : "ver consulta"}
          </button>
          {showSql ? (
            <pre className="mt-1 overflow-auto rounded bg-gray-100 p-2 text-gray-700">{sql}</pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run components/SqlTable.test.tsx`
Expected: PASS.

- [ ] **Step 5: Test del reducer para `table` — agregar a `frontend/lib/messageParts.test.ts`**

```ts
test("table events become a praxia_sql_table artifact", () => {
  const table = { columns: ["c"], rows: [{ c: "x" }], sql: "SELECT c FROM t" };
  const s = reduceEvent(initialPartsState, { type: "table", table });
  expect(s.artifacts).toEqual([
    { toolName: "praxia_sql_table", data: { columns: ["c"], rows: [{ c: "x" }], sql: "SELECT c FROM t" } },
  ]);
});
```

- [ ] **Step 6: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: FAIL (el reducer ignora `table` por el `default`).

- [ ] **Step 7: Agregar el caso `table` a `reduceEvent` en `frontend/lib/messageParts.ts`**

Insertar antes del `default:`:
```ts
    case "table":
      return {
        ...state,
        artifacts: [
          ...state.artifacts,
          {
            toolName: "praxia_sql_table",
            data: {
              columns: event.table.columns,
              rows: event.table.rows,
              sql: event.table.sql,
            },
          },
        ],
      };
```

- [ ] **Step 8: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: PASS.

- [ ] **Step 9: Registrar el Tool UI — agregar a `frontend/components/toolUIs.tsx`**

```tsx
import { SqlTable } from "./SqlTable";
```
```tsx
export const SqlTableToolUI = makeAssistantToolUI<
  { columns: string[]; rows: Record<string, unknown>[]; sql?: string },
  unknown
>({
  toolName: "praxia_sql_table",
  render: ({ args }) => <SqlTable columns={args.columns} rows={args.rows} sql={args.sql} />,
});
```

- [ ] **Step 10: Agregar el Tool UI al `<Thread>` en `frontend/app/page.tsx`**

```tsx
import { SourcesToolUI, SqlTableToolUI } from "../components/toolUIs";
```
```tsx
            <Thread tools={[SourcesToolUI, SqlTableToolUI]} />
```

- [ ] **Step 11: Suite front + lint + build**

Run:
```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```
Expected: todo PASA.

- [ ] **Step 12: Verificación MANUAL end-to-end**

Con backend + Ollama + front en dev: consulta que devuelva varias filas, p. ej. "listame los turnos de esta semana" o "¿qué clientes tengo?".
Expected: aparece una frase breve + una **tabla** estilada (header sticky, filas), y el toggle "ver consulta" muestra el SELECT. Una consulta escalar ("¿cuántos turnos esta semana?") muestra sólo la frase (sin tabla).

- [ ] **Step 13: Commit**

```bash
git add frontend/components/SqlTable.tsx frontend/components/SqlTable.test.tsx frontend/lib/messageParts.ts frontend/lib/messageParts.test.ts frontend/components/toolUIs.tsx frontend/app/page.tsx
git commit -m "feat(canvas): tabla SQL inline (praxia_sql_table tool UI)"
```

---

## Task 7: ConfirmCard por-kind inline

Mueve la confirmación al flujo de mensajes (tool-call `praxia_confirm`) y elimina el camino viejo (`pending`/`onConfirm`). El backend de confirmación NO cambia (HITL intacto).

**Files:**
- Modify: `frontend/components/ConfirmCard.tsx`
- Modify: `frontend/components/ConfirmCard.test.tsx`
- Modify: `frontend/lib/messageParts.ts`
- Modify: `frontend/lib/messageParts.test.ts`
- Modify: `frontend/components/toolUIs.tsx`
- Modify: `frontend/lib/runtime.ts`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `reduceEvent` (Task 2), `resumeChat`/`ProposedAction` (chatStream).
- Produces: `cardFields(action): CardView` (pura); `ConfirmCard({ threadId, action })`; `ConfirmToolUI` (`praxia_confirm`); `reduceEvent` maneja `type:"confirm"`; `useChatRuntime()` sin argumentos.

- [ ] **Step 1: Reescribir `frontend/components/ConfirmCard.test.tsx`**

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as chatStream from "../lib/chatStream";
import { cardFields, ConfirmCard } from "./ConfirmCard";

afterEach(() => vi.restoreAllMocks());

test("cardFields for create_appointment shows readable fields and hides ids", () => {
  const view = cardFields({
    kind: "create_appointment",
    summary: "x",
    params: {
      client_id: "c1",
      client_name: "Ana López",
      practitioner_id: "p1",
      practitioner_name: "Dra. Gómez",
      start_at: "2026-07-10T10:00:00+00:00",
      end_at: "2026-07-10T10:30:00+00:00",
      reason: "control",
    },
  });
  expect(view.title).toBe("Agendar turno");
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("Cliente");
  expect(labels).toContain("Profesional");
  expect(labels).toContain("Cuándo");
  expect(labels).toContain("Motivo");
  // no exponer ids internos
  const values = view.rows.map((r) => r.value).join(" ");
  expect(values).not.toContain("c1");
  expect(values).not.toContain("p1");
});

test("cardFields for reschedule shows old → new", () => {
  const view = cardFields({
    kind: "reschedule_appointment",
    summary: "x",
    params: {
      appointment_id: "a1",
      client_name: "Ana",
      practitioner_name: "Dr. X",
      old_start_at: "2026-07-10T10:00:00+00:00",
      new_start_at: "2026-07-12T15:00:00+00:00",
    },
  });
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("De");
  expect(labels).toContain("A");
});

test("cardFields for cancel is destructive", () => {
  const view = cardFields({
    kind: "cancel_appointment",
    summary: "x",
    params: { appointment_id: "a1", client_name: "Ana", practitioner_name: "Dr. X", start_at: "2026-07-10T10:00:00+00:00" },
  });
  expect(view.destructive).toBe(true);
});

test("cardFields for update_client lists changed fields with labels", () => {
  const view = cardFields({
    kind: "update_client",
    summary: "x",
    params: { client_id: "c1", client_name: "Ana", phone: "099-123", status: "activo" },
  });
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("Teléfono");
  expect(labels).toContain("Estado");
  expect(view.rows.find((r) => r.label === "Teléfono")?.value).toBe("099-123");
});

test("confirm streams the receipt via resumeChat", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Turno creado: Ana López" };
      yield { type: "done" };
    })(),
  );
  render(
    <ConfirmCard
      threadId="t1"
      action={{
        kind: "create_appointment",
        summary: "x",
        params: { client_name: "Ana López", practitioner_name: "Dra. Gómez", start_at: "2026-07-10T10:00:00+00:00", end_at: "2026-07-10T10:30:00+00:00" },
      }}
    />,
  );
  expect(screen.getByText("Agendar turno")).toBeTruthy();
  expect(screen.getByText("Ana López")).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "confirm"));
  await waitFor(() => expect(screen.getByText(/Turno creado/)).toBeTruthy());
});

test("cancel calls resumeChat with cancel", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "Listo, dejé el turno como estaba." };
      yield { type: "done" };
    })(),
  );
  render(
    <ConfirmCard
      threadId="t9"
      action={{ kind: "cancel_appointment", summary: "x", params: { client_name: "Ana", practitioner_name: "Dr. X", start_at: "2026-07-10T10:00:00+00:00" } }}
    />,
  );
  fireEvent.click(screen.getByText("Cancelar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t9", "cancel"));
});
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run components/ConfirmCard.test.tsx`
Expected: FAIL (`cardFields` no existe; la firma cambió).

- [ ] **Step 3: Reescribir `frontend/components/ConfirmCard.tsx`**

```tsx
"use client";

import { useState } from "react";
import { resumeChat, type ProposedAction } from "../lib/chatStream";

export interface CardRow {
  label: string;
  value: string;
}
export interface CardView {
  title: string;
  destructive: boolean;
  rows: CardRow[];
}

const FIELD_LABELS: Record<string, string> = {
  phone: "Teléfono",
  email: "Email",
  status: "Estado",
  dob: "Fecha de nacimiento",
};

function fmtDateTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}/${p(d.getUTCMonth() + 1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

function fmtRange(startIso: string, endIso: string): string {
  if (!startIso) return "";
  const base = fmtDateTime(startIso).replace(" UTC", "");
  if (!endIso) return `${base} UTC`;
  const e = new Date(endIso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${base}–${p(e.getUTCHours())}:${p(e.getUTCMinutes())} UTC`;
}

export function cardFields(action: ProposedAction): CardView {
  const p = action.params as Record<string, unknown>;
  const s = (k: string) => (p[k] == null ? "" : String(p[k]));
  switch (action.kind) {
    case "create_appointment": {
      const rows: CardRow[] = [
        { label: "Cliente", value: s("client_name") },
        { label: "Profesional", value: s("practitioner_name") },
        { label: "Cuándo", value: fmtRange(s("start_at"), s("end_at")) },
      ];
      if (p.reason) rows.push({ label: "Motivo", value: s("reason") });
      if (p.channel) rows.push({ label: "Canal", value: s("channel") });
      return { title: "Agendar turno", destructive: false, rows };
    }
    case "reschedule_appointment":
      return {
        title: "Reprogramar turno",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Profesional", value: s("practitioner_name") },
          { label: "De", value: fmtDateTime(s("old_start_at")) },
          { label: "A", value: fmtDateTime(s("new_start_at")) },
        ],
      };
    case "cancel_appointment":
      return {
        title: "Cancelar turno",
        destructive: true,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Profesional", value: s("practitioner_name") },
          { label: "Turno", value: fmtDateTime(s("start_at")) },
        ],
      };
    case "log_interaction":
      return {
        title: "Registrar interacción",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Tipo", value: s("type") },
          { label: "Contenido", value: s("content") },
        ],
      };
    case "update_client": {
      const changed = ["phone", "email", "status", "dob"].filter(
        (k) => p[k] != null && p[k] !== "",
      );
      return {
        title: "Actualizar cliente",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          ...changed.map((k) => ({ label: FIELD_LABELS[k], value: s(k) })),
        ],
      };
    }
    default:
      return { title: "Confirmar acción", destructive: false, rows: [{ label: "", value: action.summary }] };
  }
}

export function ConfirmCard({ threadId, action }: { threadId: string; action: ProposedAction }) {
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [receipt, setReceipt] = useState("");
  const view = cardFields(action);

  async function decide(decision: "confirm" | "cancel") {
    setPhase("working");
    let text = "";
    try {
      for await (const ev of resumeChat(threadId, decision)) {
        if (ev.type === "token") {
          text += ev.text;
          setReceipt(text);
        }
      }
    } catch (err) {
      setReceipt(err instanceof Error ? err.message : "No se pudo completar la acción.");
    }
    setPhase("done");
  }

  return (
    <div
      className={`my-2 rounded-lg border p-3 ${
        view.destructive ? "border-red-200 bg-red-50" : "border-gray-200 bg-gray-50"
      }`}
    >
      <p className="mb-2 font-semibold text-gray-800">{view.title}</p>
      <dl className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        {view.rows.map((r, i) => (
          <div key={i} className="contents">
            <dt className="text-gray-500">{r.label}</dt>
            <dd className="whitespace-pre-wrap text-gray-800">{r.value}</dd>
          </div>
        ))}
      </dl>
      {phase !== "done" ? (
        <div className="flex gap-2">
          <button
            onClick={() => decide("confirm")}
            disabled={phase === "working"}
            className={`rounded px-3 py-1 text-sm font-medium text-white disabled:opacity-50 ${
              view.destructive ? "bg-red-600" : "bg-blue-600"
            }`}
          >
            Confirmar
          </button>
          <button
            onClick={() => decide("cancel")}
            disabled={phase === "working"}
            className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 disabled:opacity-50"
          >
            Cancelar
          </button>
        </div>
      ) : (
        <p className="whitespace-pre-wrap text-sm text-gray-800">{receipt}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run components/ConfirmCard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Test del reducer para `confirm` — agregar a `frontend/lib/messageParts.test.ts`**

```ts
test("confirm events become a praxia_confirm artifact", () => {
  const action = { kind: "cancel_appointment", summary: "x", params: { client_name: "Ana" } };
  const s = reduceEvent(initialPartsState, { type: "confirm", threadId: "t1", action });
  expect(s.artifacts).toEqual([
    { toolName: "praxia_confirm", data: { threadId: "t1", action } },
  ]);
});
```

- [ ] **Step 6: Correr el test (debe fallar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: FAIL (el reducer no maneja `confirm`).

- [ ] **Step 7: Agregar el caso `confirm` a `reduceEvent` en `frontend/lib/messageParts.ts`**

Insertar antes del `default:`:
```ts
    case "confirm":
      return {
        ...state,
        artifacts: [
          ...state.artifacts,
          { toolName: "praxia_confirm", data: { threadId: event.threadId, action: event.action } },
        ],
      };
```

- [ ] **Step 8: Correr el test (debe pasar)**

Run: `npm --prefix frontend run test -- --run lib/messageParts.test.ts`
Expected: PASS.

- [ ] **Step 9: Registrar el Tool UI — agregar a `frontend/components/toolUIs.tsx`**

```tsx
import { ConfirmCard } from "./ConfirmCard";
import type { ProposedAction } from "../lib/chatStream";
```
```tsx
export const ConfirmToolUI = makeAssistantToolUI<{ threadId: string; action: ProposedAction }, unknown>({
  toolName: "praxia_confirm",
  render: ({ args }) => <ConfirmCard threadId={args.threadId} action={args.action} />,
});
```

- [ ] **Step 10: Quitar el camino viejo del confirm en `frontend/lib/runtime.ts`**

Reemplazar el archivo completo por (sin `onConfirm`/`PendingAction`; el confirm pasa por el reducer):
```tsx
"use client";

import { useMemo, useRef } from "react";
import {
  useLocalRuntime,
  type ChatModelAdapter,
  type ChatModelRunOptions,
  type ChatModelRunResult,
  type ThreadUserMessage,
} from "@assistant-ui/react";
import { streamChat } from "./chatStream";
import { initialPartsState, reduceEvent, toContent, type PartsState } from "./messageParts";

function lastUserText(messages: ChatModelRunOptions["messages"]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const userMsg = msg as ThreadUserMessage;
      return userMsg.content.map((p) => (p.type === "text" ? p.text : "")).join("");
    }
  }
  return "";
}

export function useChatRuntime() {
  const threadIdRef = useRef<string | undefined>(undefined);
  if (!threadIdRef.current) threadIdRef.current = crypto.randomUUID();
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({
        messages,
        abortSignal,
      }: ChatModelRunOptions): AsyncGenerator<ChatModelRunResult, void> {
        const query = lastUserText(messages);
        let state: PartsState = initialPartsState;
        try {
          for await (const ev of streamChat(query, threadIdRef.current!, abortSignal)) {
            state = reduceEvent(state, ev);
            yield { content: toContent(state) };
          }
        } catch (err) {
          if (abortSignal?.aborted) return;
          const message = err instanceof Error ? err.message : "No se pudo contactar al asistente.";
          yield { content: [{ type: "text", text: message }] };
          return;
        }
        yield { content: toContent(state), status: { type: "complete", reason: "stop" } };
      },
    }),
    [],
  );
  return useLocalRuntime(adapter);
}
```

- [ ] **Step 11: Simplificar `frontend/app/page.tsx` (confirm inline; quitar `pending`/`onConfirm`)**

Reemplazar el archivo completo por (mantiene estilos inline actuales; la conversión a Tailwind es la Task 8):
```tsx
"use client";

import { useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";
import { SourcesToolUI, SqlTableToolUI, ConfirmToolUI } from "../components/toolUIs";

export default function Home() {
  const [refreshKey, setRefreshKey] = useState(0);
  const runtime = useChatRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100vh" }}>
        <aside style={{ padding: 16, borderRight: "1px solid #ddd", overflowY: "auto" }}>
          <h1 style={{ fontSize: 18 }}>Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 style={{ fontSize: 14, marginTop: 16 }}>Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, minHeight: 0 }}>
            <Thread tools={[SourcesToolUI, SqlTableToolUI, ConfirmToolUI]} />
          </div>
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
```

- [ ] **Step 12: Suite front + lint + build**

Run:
```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```
Expected: todo PASA (no queda referencia a `PendingAction`/`onConfirm`).

- [ ] **Step 13: Verificación MANUAL — HITL intacto**

Con backend + Ollama + front: pedir una escritura, p. ej. "agendá un turno para Ana mañana a las 10".
Expected: aparece una **ConfirmCard inline** con título "Agendar turno" y campos legibles (cliente, profesional, cuándo). Al **Confirmar** aparece el recibo; **la escritura NO ocurre sin confirmar** (HITL airtight). Probar también un cancel (card destructiva roja).

- [ ] **Step 14: Commit**

```bash
git add frontend/components/ConfirmCard.tsx frontend/components/ConfirmCard.test.tsx frontend/lib/messageParts.ts frontend/lib/messageParts.test.ts frontend/components/toolUIs.tsx frontend/lib/runtime.ts frontend/app/page.tsx
git commit -m "feat(canvas): ConfirmCard por-kind inline (praxia_confirm tool UI)"
```

---

## Task 8: i18n + conversión Tailwind del layout

**Files:**
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: los 3 Tool UIs (Tasks 2/6/7).

- [ ] **Step 1: Convertir `page.tsx` a Tailwind + agregar `strings`/`welcome`**

Reemplazar el archivo completo por:
```tsx
"use client";

import { useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";
import { SourcesToolUI, SqlTableToolUI, ConfirmToolUI } from "../components/toolUIs";

export default function Home() {
  const [refreshKey, setRefreshKey] = useState(0);
  const runtime = useChatRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main className="grid h-screen grid-cols-[320px_1fr]">
        <aside className="overflow-y-auto border-r border-gray-200 p-4">
          <h1 className="text-lg font-semibold">Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 className="mt-4 text-sm font-medium text-gray-700">Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section className="flex h-screen min-h-0 flex-col">
          <div className="min-h-0 flex-1">
            <Thread
              tools={[SourcesToolUI, SqlTableToolUI, ConfirmToolUI]}
              welcome={{
                message:
                  "Hola 👋 Preguntame por tu agenda o tus documentos, o pedime agendar, reprogramar, cancelar, registrar o actualizar datos.",
              }}
              strings={{
                composer: {
                  input: { placeholder: "Escribí tu mensaje…" },
                  send: { tooltip: "Enviar" },
                },
              }}
            />
          </div>
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
```

- [ ] **Step 2: Suite front + lint + build**

Run:
```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```
Expected: todo PASA.

- [ ] **Step 3: Verificación MANUAL**

Con front en dev: la app se ve con el layout correcto (sidebar + chat), el mensaje de bienvenida en español y el placeholder "Escribí tu mensaje…".
Expected: sin regresiones visuales; drop zone y lista de documentos siguen funcionando.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat(canvas): i18n del Thread + layout en Tailwind"
```

---

## Task 9: Gate completo + smoke + SMOKE.md

**Files:**
- Modify: `frontend/SMOKE.md`

- [ ] **Step 1: Gate backend completo**

Run:
```bash
backend\.venv\Scripts\python -m ruff format backend/app backend/tests
backend\.venv\Scripts\python -m ruff check backend/app backend/tests
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q
```
Expected: todo limpio; gate no-llm no regresiona.

- [ ] **Step 2: Gate frontend completo**

Run:
```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```
Expected: todo PASA.

- [ ] **Step 3: Smoke manual end-to-end**

Con Ollama + `docker compose up -d` + schema/seed + `backend\.venv\Scripts\python backend\dev.py` + `npm --prefix frontend run dev`:
1. Consulta documental → respuesta + **citas** numeradas (`<Citations>`).
2. Consulta SQL multi-fila ("¿qué clientes tengo?") → frase + **tabla** (`<SqlTable>`, toggle "ver consulta"). Consulta escalar ("¿cuántos turnos esta semana?") → sólo frase.
3. Escritura ("agendá un turno para Ana mañana 10") → **ConfirmCard** rica; **sigue pidiendo confirmación** (HITL). Confirmar → recibo. Cancelar → card destructiva.
4. Chitchat ("hola") → respuesta breve, sin artefactos.

- [ ] **Step 4: Actualizar `frontend/SMOKE.md`**

Agregar una sección "Canvas rico" con los 4 checks del Step 3 (citas inline, tabla SQL + toggle, ConfirmCard por-kind con HITL, chitchat sin artefactos).

- [ ] **Step 5: Commit**

```bash
git add frontend/SMOKE.md
git commit -m "docs(canvas): checks de smoke del canvas rico"
```

---

## Self-Review

**1. Spec coverage:**
- §2/§4 render inline vía tool-call + Tool UIs → Tasks 2/6/7. ✓
- §2 tabla SQL real + evento `table` → Tasks 3/4/5/6. ✓
- §2 citas RAG ricas → Task 2. ✓
- §2 ConfirmCards por-kind → Task 7 (`cardFields` cubre los 5 kinds + IDs ocultos). ✓
- §3 estilos Tailwind → Task 1 + clases en cada componente. ✓
- §5.1 evento `table` con `default=str` → Task 4 Step 7 + test Step 5. ✓
- §5.2 reducer puro `evento→parts`, texto primero → `messageParts.ts` (Tasks 2/6/7). ✓
- §5.4 campos por-kind (reschedule usa `old_start_at`; update_client valores nuevos; sin IDs) → Task 7. ✓
- §6 backend sin tocar `propose_*`/HITL/RAG/router → sólo `sql_present`/`nodes`/`main`. ✓
- §8 tests front (SqlTable, Citations, ConfirmCard, reducer, chatStream) + back (sql_node table, main forward, sql_present) → cubiertos. ✓
- §10.1 de-risk del mecanismo → Task 2 Step 13 (con fallback). ✓
- No-goals (fichas, before→after, visor docs, markdown prosa, panel) → no aparecen en ninguna task. ✓

**2. Placeholder scan:** sin TBD/TODO; todo step de código muestra código completo; comandos con expected. ✓

**3. Type consistency:** `reduceEvent`/`toContent`/`initialPartsState`/`PartsState`/`ArtifactPart` consistentes entre `messageParts.ts` y sus consumidores; `SqlTablePayload` (Task 5) consumido por reducer (Task 6) y `SqlTable`; toolNames `praxia_sources`/`praxia_sql_table`/`praxia_confirm` idénticos entre reducer, `toolUIs.tsx` y el backend emitter; `cardFields`/`ConfirmCard({threadId, action})` consistentes entre componente, test y `ConfirmToolUI`; `write_table(columns, rows, sql)` idéntico en `nodes` (emit) y `main` (consume keys `columns`/`rows`/`sql`). ✓

---

## Execution Handoff

Plan completo y guardado en `docs/superpowers/plans/2026-07-01-rich-canvas.md`. Dos opciones de ejecución:

1. **Subagent-Driven (recomendado)** — despacho un subagente fresco por task, review entre tasks, iteración rápida.
2. **Inline Execution** — ejecuto las tasks en esta sesión con executing-plans, en lotes con checkpoints.

¿Cuál preferís?
