# Fase 0 · Slice Mínimo · Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal Next.js + assistant-ui frontend that completes Praxia's Fase 0 vertical slice — drop a document, watch it index, ask a question, and see a streamed answer with citations — talking only to the local backend.

**Architecture:** Next.js (App Router, TypeScript). A `next.config` rewrite proxies `/api/*` to the FastAPI backend at `http://localhost:8000/*` (avoids CORS). A typed API client (`lib/api.ts`) handles ingest + document listing; an async-generator SSE reader (`lib/chatStream.ts`) consumes `/api/chat`; an assistant-ui local-runtime adapter (`lib/runtime.ts`) bridges that stream into the `<Thread>` UI. A `<DropZone>` + `<DocumentList>` handle uploads. Vitest unit-tests the `lib/` logic; the integrated UI is verified by a documented manual browser smoke.

**Tech Stack:** Next.js 15 (App Router), React 19, TypeScript, `@assistant-ui/react`, Vitest, `@testing-library/react` (for the one component test). Node 22 / npm 11 (already installed).

## Global Constraints

- **Local-first / $0:** the frontend talks ONLY to the local backend via the `/api` proxy. No external calls, no analytics. **Disable Next telemetry** (`npx next telemetry disable` or `NEXT_TELEMETRY_DISABLED=1`). (CLAUDE.md §0)
- **`practice_id` is server-side.** The frontend NEVER sends `practice_id` — `/chat` body is `{message}` only; `/ingest` sends `file`, `doc_type`, `title`. (spec §8)
- **Backend API contract (exact):**
  - `POST /api/ingest` — multipart: `file`, form `doc_type`, `title` → `200 {document_id, status, n_chunks}`; `415` unsupported type; `422` parse error.
  - `GET /api/documents` → `[{id, title, doc_type, status, page_count, ingested_at}]`.
  - `POST /api/chat` — JSON `{message}` → SSE: zero or more `event: token` (`data:` = text delta), then one `event: sources` (`data:` = JSON `[{n,title,page,document_id}]`), then `event: done` (`data: [DONE]`).
  - `GET /api/health` → `{status:"ok"}`.
- **Commits clean:** NO `Co-Authored-By: Claude`, no assistant attribution. (CLAUDE.md §6)
- **Gate per task:** `npm run lint` clean, `npm run build` succeeds, `npm run test` (vitest) green.

**Spec:** `docs/superpowers/specs/2026-06-24-fase0-slice-minimo-design.md` · **Backend (built):** `docs/superpowers/plans/2026-06-24-fase0-slice-minimo-backend.md`

> **Prerequisite:** the backend must be running for the integrated smoke: `backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend` (port 8000). The full "cited answer" demo also needs Ollama running with the model pulled; without it, the chat path still streams (you'll get the abstention message), which exercises all the UI wiring.

---

## File Structure

```
frontend/
├── package.json
├── next.config.mjs          # rewrites /api/* -> http://localhost:8000/*
├── tsconfig.json
├── vitest.config.ts
├── .gitignore               # (or rely on root .gitignore: node_modules, .next)
├── app/
│   ├── layout.tsx           # AssistantRuntimeProvider wrapper
│   ├── page.tsx             # DropZone + DocumentList + Thread
│   └── globals.css
├── components/
│   ├── DropZone.tsx         # drag&drop -> ingest, shows status
│   └── DocumentList.tsx     # lists /api/documents
└── lib/
    ├── api.ts               # types + ingestDocument(), listDocuments()
    ├── api.test.ts
    ├── chatStream.ts        # async-generator SSE reader for /api/chat
    ├── chatStream.test.ts
    └── runtime.ts           # assistant-ui ChatModelAdapter
```

All commands run from `frontend/` unless noted.

---

## Task 1: Scaffold Next.js + Vitest + proxy

**Files:**
- Create: `frontend/package.json`, `frontend/next.config.mjs`, `frontend/tsconfig.json`, `frontend/vitest.config.ts`, `frontend/app/layout.tsx`, `frontend/app/page.tsx`, `frontend/app/globals.css`, `frontend/lib/smoke.test.ts`

**Interfaces:**
- Consumes: the running backend (`/api/health`).
- Produces: a runnable Next app; the `/api/*` proxy; a working `npm run test`/`lint`/`build`.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "praxia-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "15.1.3",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "@assistant-ui/react": "^0.7.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "@types/node": "^22.10.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "eslint": "^9.17.0",
    "eslint-config-next": "15.1.3",
    "vitest": "^2.1.8",
    "jsdom": "^25.0.1",
    "@testing-library/react": "^16.1.0",
    "@testing-library/dom": "^10.4.0"
  }
}
```

> **Implementer note:** if `npm install` reports that an exact version above no longer resolves, install the nearest available patch/minor of the SAME major and record the resolved version in your report — do not change majors. `@assistant-ui/react` evolves quickly; pin whatever `npm install @assistant-ui/react` resolves and note it (Task 4 depends on its API).

- [ ] **Step 2: Create `frontend/next.config.mjs`** (proxy to backend)

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/:path*" },
    ];
  },
};

export default nextConfig;
```

- [ ] **Step 3: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 4: Create `frontend/vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

- [ ] **Step 5: Create `frontend/app/globals.css`** (minimal)

```css
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; font-family: system-ui, sans-serif; }
```

- [ ] **Step 6: Create `frontend/app/layout.tsx`**

```tsx
import "./globals.css";

export const metadata = { title: "Praxia · Fase 0" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 7: Create a placeholder `frontend/app/page.tsx`** (replaced in Task 6)

```tsx
export default function Home() {
  return <main style={{ padding: 24 }}>Praxia — Fase 0 (scaffold)</main>;
}
```

- [ ] **Step 8: Write a trivial passing test** — `frontend/lib/smoke.test.ts`

```typescript
import { expect, test } from "vitest";

test("vitest runs", () => {
  expect(1 + 1).toBe(2);
});
```

- [ ] **Step 9: Install, lint, test, build**

```bash
cd frontend
export NEXT_TELEMETRY_DISABLED=1
npm install
npm run test
npm run build
```
Expected: `npm install` succeeds; vitest reports 1 passed; `next build` completes with no errors. (`next lint` may prompt to set up ESLint on first run — accept the strict config or add `eslint-config-next`; it's already in devDependencies.)

- [ ] **Step 10: Manual proxy check** (backend must be running on :8000)

```bash
npm run dev   # in one terminal
# in another:
curl http://localhost:3000/api/health
```
Expected: `{"status":"ok"}` (proves the `/api` rewrite reaches the backend).

- [ ] **Step 11: Commit**

```bash
git add frontend/
git commit -m "chore(front): scaffold Next.js + vitest + proxy al backend"
```

---

## Task 2: API client (`lib/api.ts`)

**Files:**
- Create: `frontend/lib/api.ts`, `frontend/lib/api.test.ts`

**Interfaces:**
- Consumes: `/api/ingest`, `/api/documents`.
- Produces:
  - types `DocumentSummary { document_id: string; status: string; n_chunks: number }`, `DocumentRow { id: string; title: string; doc_type: string; status: string; page_count: number | null; ingested_at: string }`
  - `async ingestDocument(file: File, docType: string, title: string): Promise<DocumentSummary>` — POSTs multipart; throws `Error` with the backend detail on non-2xx (415/422).
  - `async listDocuments(): Promise<DocumentRow[]>`

- [ ] **Step 1: Write the failing test** — `frontend/lib/api.test.ts`

```typescript
import { afterEach, expect, test, vi } from "vitest";
import { ingestDocument, listDocuments } from "./api";

afterEach(() => vi.restoreAllMocks());

test("ingestDocument posts multipart and returns the summary", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ document_id: "d1", status: "indexado", n_chunks: 3 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const file = new File(["# Protocolo"], "protocolo.md", { type: "text/markdown" });
  const out = await ingestDocument(file, "protocolo", "Protocolo");

  expect(out.status).toBe("indexado");
  expect(out.n_chunks).toBe(3);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/ingest");
  expect(init.method).toBe("POST");
  expect(init.body).toBeInstanceOf(FormData);
});

test("ingestDocument throws the backend detail on error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "Tipo no soportado: foto.png" }), { status: 415 }),
  ));
  const file = new File(["x"], "foto.png", { type: "image/png" });
  await expect(ingestDocument(file, "protocolo", "Foto")).rejects.toThrow("Tipo no soportado");
});

test("listDocuments returns the array", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify([{ id: "d1", title: "P", doc_type: "protocolo", status: "indexado", page_count: 1, ingested_at: "2026-06-24T00:00:00Z" }]), { status: 200 }),
  ));
  const docs = await listDocuments();
  expect(docs).toHaveLength(1);
  expect(docs[0].title).toBe("P");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- lib/api.test.ts`
Expected: FAIL (cannot import `./api`).

- [ ] **Step 3: Implement `frontend/lib/api.ts`**

```typescript
export interface DocumentSummary {
  document_id: string;
  status: string;
  n_chunks: number;
}

export interface DocumentRow {
  id: string;
  title: string;
  doc_type: string;
  status: string;
  page_count: number | null;
  ingested_at: string;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : `Error ${res.status}`;
  } catch {
    return `Error ${res.status}`;
  }
}

export async function ingestDocument(
  file: File,
  docType: string,
  title: string,
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("doc_type", docType);
  form.append("title", title);
  const res = await fetch("/api/ingest", { method: "POST", body: form });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as DocumentSummary;
}

export async function listDocuments(): Promise<DocumentRow[]> {
  const res = await fetch("/api/documents");
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as DocumentRow[];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- lib/api.test.ts`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/api.test.ts
git commit -m "feat(front): cliente API (ingest, documents)"
```

---

## Task 3: SSE chat stream reader (`lib/chatStream.ts`)

**Files:**
- Create: `frontend/lib/chatStream.ts`, `frontend/lib/chatStream.test.ts`

**Interfaces:**
- Consumes: `/api/chat` (SSE).
- Produces:
  - type `Source { n: number; title: string; page: number | null; document_id: string }`
  - type `ChatEvent = { type: "token"; text: string } | { type: "sources"; sources: Source[] } | { type: "done" }`
  - `async function* streamChat(message: string, signal?: AbortSignal): AsyncGenerator<ChatEvent>` — POSTs `{message}`, parses the SSE byte stream (handling events split across chunks), yields one `ChatEvent` per SSE event.

- [ ] **Step 1: Write the failing test** — `frontend/lib/chatStream.test.ts`

```typescript
import { expect, test, vi } from "vitest";
import { streamChat } from "./chatStream";

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

test("streamChat yields tokens, sources, done — even across split chunks", async () => {
  // 'event: token' for "Hola" is deliberately split mid-event to test buffering.
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
    "event: token\ndata: Ho",
    "la\n\nevent: token\ndata:  mundo\n\n",
    'event: sources\ndata: [{"n":1,"title":"Protocolo","page":2,"document_id":"d1"}]\n\n',
    "event: done\ndata: [DONE]\n\n",
  ])));

  const events = [];
  for await (const ev of streamChat("¿hola?")) events.push(ev);

  expect(events).toEqual([
    { type: "token", text: "Hola" },
    { type: "token", text: " mundo" },
    { type: "sources", sources: [{ n: 1, title: "Protocolo", page: 2, document_id: "d1" }] },
    { type: "done" },
  ]);
});

test("streamChat throws on non-ok response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 503 })));
  await expect(async () => {
    for await (const _ of streamChat("x")) { /* drain */ }
  }).rejects.toThrow();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- lib/chatStream.test.ts`
Expected: FAIL (cannot import `./chatStream`).

- [ ] **Step 3: Implement `frontend/lib/chatStream.ts`**

```typescript
export interface Source {
  n: number;
  title: string;
  page: number | null;
  document_id: string;
}

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "done" };

function parseEvent(raw: string): ChatEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  const data = dataLines.join("\n");
  if (event === "token") return { type: "token", text: data };
  if (event === "sources") return { type: "sources", sources: JSON.parse(data) as Source[] };
  if (event === "done") return { type: "done" };
  return null;
}

export async function* streamChat(
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseEvent(raw);
      if (ev) yield ev;
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- lib/chatStream.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/chatStream.ts frontend/lib/chatStream.test.ts
git commit -m "feat(front): lector SSE de /api/chat (async generator, buffering)"
```

---

## Task 4: assistant-ui runtime adapter (`lib/runtime.ts`)

**Files:**
- Create: `frontend/lib/runtime.ts`

**Interfaces:**
- Consumes: `streamChat` (Task 3), `@assistant-ui/react`.
- Produces: `useChatRuntime()` — a hook returning an assistant-ui runtime whose adapter streams answers from the backend and appends a "Fuentes" block from the `sources` event.

> **⚠️ Version-sensitive integration — verify against the installed `@assistant-ui/react` API.** The code below targets the `useLocalRuntime(adapter)` + `ChatModelAdapter` shape (adapter has an `async *run({ messages, abortSignal })` that yields `{ content: [{ type: "text", text }] }`, each yield being the FULL accumulated content). Before implementing, confirm in `node_modules/@assistant-ui/react` (or the version's docs) that: (a) `useLocalRuntime` and the `ChatModelAdapter` type are exported and the `run` signature matches; (b) how to read the latest user text from `messages`. If the installed version differs, adapt the adapter to its API but keep the behavior identical (stream tokens, then append sources). Record any deviation in your report.

- [ ] **Step 1: Implement `frontend/lib/runtime.ts`**

```typescript
"use client";

import { useLocalRuntime, type ChatModelAdapter } from "@assistant-ui/react";
import { streamChat, type Source } from "./chatStream";

function lastUserText(messages: readonly { role: string; content: readonly unknown[] }[]): string {
  const last = messages[messages.length - 1];
  if (!last) return "";
  return (last.content as { type: string; text?: string }[])
    .map((p) => (p.type === "text" ? (p.text ?? "") : ""))
    .join("");
}

function sourcesBlock(sources: Source[]): string {
  if (sources.length === 0) return "";
  const lines = sources.map(
    (s) => `[${s.n}] ${s.title}${s.page != null ? ` — p.${s.page}` : ""}`,
  );
  return `\n\n**Fuentes:**\n${lines.join("\n")}`;
}

const adapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const query = lastUserText(messages as never);
    let answer = "";
    let sources: Source[] = [];
    for await (const ev of streamChat(query, abortSignal)) {
      if (ev.type === "token") {
        answer += ev.text;
        yield { content: [{ type: "text", text: answer }] };
      } else if (ev.type === "sources") {
        sources = ev.sources;
      }
    }
    yield { content: [{ type: "text", text: answer + sourcesBlock(sources) }] };
  },
};

export function useChatRuntime() {
  return useLocalRuntime(adapter);
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. If `@assistant-ui/react` types reject the adapter shape, adapt per the version note above and re-run.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/runtime.ts
git commit -m "feat(front): adapter de runtime assistant-ui sobre el stream del backend"
```

---

## Task 5: DropZone + DocumentList components

**Files:**
- Create: `frontend/components/DropZone.tsx`, `frontend/components/DocumentList.tsx`, `frontend/components/DropZone.test.tsx`

**Interfaces:**
- Consumes: `ingestDocument`, `listDocuments` (Task 2).
- Produces:
  - `<DropZone onIngested={() => void} />` — a drag&drop + file-input area that calls `ingestDocument` (doc_type defaults to `"protocolo"`, title = file name), shows `procesando…` / `indexado` / error, then calls `onIngested`.
  - `<DocumentList refreshKey={number} />` — fetches and lists documents; re-fetches when `refreshKey` changes.

- [ ] **Step 1: Write the failing component test** — `frontend/components/DropZone.test.tsx`

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as api from "../lib/api";
import { DropZone } from "./DropZone";

afterEach(() => vi.restoreAllMocks());

test("uploading a file calls ingestDocument and reports indexado", async () => {
  vi.spyOn(api, "ingestDocument").mockResolvedValue({
    document_id: "d1", status: "indexado", n_chunks: 2,
  });
  const onIngested = vi.fn();
  render(<DropZone onIngested={onIngested} />);

  const file = new File(["# P"], "protocolo.md", { type: "text/markdown" });
  const input = screen.getByTestId("file-input") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(api.ingestDocument).toHaveBeenCalledWith(file, "protocolo", "protocolo.md"));
  await waitFor(() => expect(screen.getByText(/indexado/i)).toBeTruthy());
  expect(onIngested).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- components/DropZone.test.tsx`
Expected: FAIL (cannot import `./DropZone`).

- [ ] **Step 3: Implement `frontend/components/DropZone.tsx`**

```tsx
"use client";

import { useState } from "react";
import { ingestDocument } from "../lib/api";

export function DropZone({ onIngested }: { onIngested: () => void }) {
  const [status, setStatus] = useState<string>("");

  async function handleFile(file: File) {
    setStatus(`procesando ${file.name}…`);
    try {
      const summary = await ingestDocument(file, "protocolo", file.name);
      setStatus(`${file.name}: ${summary.status} (${summary.n_chunks} fragmentos)`);
      onIngested();
    } catch (e) {
      setStatus(`error: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) void handleFile(file);
      }}
      style={{ border: "2px dashed #999", borderRadius: 8, padding: 24, textAlign: "center" }}
    >
      <p>Soltá un PDF o MD aquí</p>
      <input
        data-testid="file-input"
        type="file"
        accept=".pdf,.md,.markdown,.txt"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      {status && <p style={{ marginTop: 12, fontSize: 14 }}>{status}</p>}
    </div>
  );
}
```

- [ ] **Step 4: Implement `frontend/components/DocumentList.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { listDocuments, type DocumentRow } from "../lib/api";

export function DocumentList({ refreshKey }: { refreshKey: number }) {
  const [docs, setDocs] = useState<DocumentRow[]>([]);

  useEffect(() => {
    let active = true;
    listDocuments()
      .then((d) => { if (active) setDocs(d); })
      .catch(() => { if (active) setDocs([]); });
    return () => { active = false; };
  }, [refreshKey]);

  if (docs.length === 0) return <p style={{ fontSize: 14, color: "#666" }}>Sin documentos aún.</p>;
  return (
    <ul style={{ fontSize: 14, paddingLeft: 18 }}>
      {docs.map((d) => (
        <li key={d.id}>{d.title} — <em>{d.status}</em></li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test -- components/DropZone.test.tsx`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/
git commit -m "feat(front): DropZone (ingesta) y DocumentList"
```

---

## Task 6: Compose the page (`app/page.tsx`, `app/layout.tsx`)

**Files:**
- Modify: `frontend/app/page.tsx`, `frontend/app/layout.tsx`

**Interfaces:**
- Consumes: `useChatRuntime` (Task 4), `<DropZone>`/`<DocumentList>` (Task 5), assistant-ui `<AssistantRuntimeProvider>` + `<Thread>`.
- Produces: the single-screen UI — drop zone + document list on one side, chat thread on the other.

> **⚠️ Version note:** confirm the import path for the styled `<Thread>` and the provider in the installed `@assistant-ui/react` (some versions export `<Thread>` from `@assistant-ui/react`, and require importing a CSS file, e.g. `@assistant-ui/react/styles/index.css`, or a Tailwind setup). If the styled `<Thread>` is not available, compose the documented Thread primitives. Keep behavior identical: a streaming chat bound to `useChatRuntime()`. Record what you used in your report.

- [ ] **Step 1: Replace `frontend/app/page.tsx`**

```tsx
"use client";

import { useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";

export default function Home() {
  const runtime = useChatRuntime();
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100vh" }}>
        <aside style={{ padding: 16, borderRight: "1px solid #ddd", overflowY: "auto" }}>
          <h1 style={{ fontSize: 18 }}>Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 style={{ fontSize: 14, marginTop: 16 }}>Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section style={{ height: "100vh" }}>
          <Thread />
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
```

- [ ] **Step 2: Ensure any required assistant-ui CSS is imported** in `app/layout.tsx` (per the version note). If the installed version needs it, add the import at the top of `layout.tsx`; if not, skip.

- [ ] **Step 3: Build + lint**

```bash
cd frontend && npm run build && npm run lint
```
Expected: build succeeds, lint clean. Fix any type/lint errors (e.g. adjust the `<Thread>` import per the installed version).

- [ ] **Step 4: Commit**

```bash
git add frontend/app/
git commit -m "feat(front): pantalla unica (DropZone + DocumentList + Thread)"
```

---

## Task 7: End-to-end manual acceptance smoke

**Files:**
- Create: `frontend/SMOKE.md` (documents the manual acceptance steps)

**Interfaces:**
- Consumes: the whole stack (backend + frontend; Ollama for the real cited answer).
- Produces: a documented, repeatable manual acceptance matching the spec's Fase 0 DoD.

- [ ] **Step 1: Write `frontend/SMOKE.md`**

```markdown
# Smoke manual — Fase 0 slice

Prerequisitos: `docker compose up -d` (Postgres+Qdrant) y Ollama corriendo con el modelo de `OLLAMA_MODEL` pulled.

1. Backend: `backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend` (http://localhost:8000)
2. Frontend: `cd frontend && npm run dev` (http://localhost:3000)
3. En el navegador (http://localhost:3000):
   - Soltá `backend/tests/fixtures/protocolo.md` en la drop zone → debe pasar a `indexado` y aparecer en "Documentos".
   - Preguntá en el chat: "¿cuánto dura la primera consulta?" → respuesta en streaming, en español, mencionando "60 minutos", con un bloque **Fuentes**.
   - Preguntá algo no cubierto: "¿cuál es la dirección de la clínica?" → mensaje de abstención.

Sin Ollama: los pasos de ingesta y el streaming SSE igual funcionan; el chat devolverá el mensaje de abstención (no hay LLM para sintetizar), lo que valida todo el cableado UI↔backend salvo la síntesis real.
```

- [ ] **Step 2: Run the manual smoke** (if Ollama is available) and confirm each step. If Ollama is not yet installed, run steps 1-3 up to the abstention to confirm UI↔backend wiring, and note in your report that the cited-answer step is pending Ollama.

- [ ] **Step 3: Commit**

```bash
git add frontend/SMOKE.md
git commit -m "docs(front): smoke manual de aceptacion Fase 0"
```

---

## Self-Review (author checklist — completed)

**Spec coverage (frontend portions of spec §5/§7):**
- assistant-ui Thread + DropZone single screen → Tasks 5-6. SSE streaming consumed → Task 3 + 4. Cited answer rendered (Fuentes block) → Task 4. Document list → Task 5. `/api` proxy (no CORS) → Task 1. `practice_id` never sent by client → enforced by `api.ts`/`chatStream.ts` payloads (Tasks 2-3). Manual acceptance (drop→ask→cited answer / abstain) → Task 7.

**Placeholder scan:** no TBD/TODO; every code step has complete code. The two `⚠️ version-sensitive` notes (Tasks 4, 6) are explicit external-API verification points for `@assistant-ui/react`, not logic placeholders — the logic (`lib/`) is fully specified and TDD'd.

**Type consistency:** `Source`/`ChatEvent` defined in `chatStream.ts` (Task 3) and consumed in `runtime.ts` (Task 4); `DocumentSummary`/`DocumentRow` defined in `api.ts` (Task 2) and consumed in components (Task 5). `useChatRuntime` (Task 4) consumed in `page.tsx` (Task 6).

**Out of scope (deferred, per spec §14):** real assistant-ui theming/canvas (tables, fichas, doc preview), attachments via assistant-ui's own composer, auth, multi-doc-type UI — all Fase 1+.

**Known risk:** `@assistant-ui/react` API is version-sensitive; Tasks 4 and 6 carry explicit verify-against-installed-version notes. This is the one integration surface most likely to need adjustment at implementation time.
