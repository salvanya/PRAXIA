# Diseño — Canvas más rico (cierre Fase 1)

- **Fecha:** 2026-07-01
- **Fase:** 1 (MVP conversacional) — **último ítem del MVP**
- **Estado:** aprobado en brainstorming; pendiente de plan (writing-plans)
- **Slice previo:** Slice 9 — guardrails PII (`6004277` + follow-up WYSIWYG `31f124f`)

## 1. Contexto y objetivo

Praxia ya tiene grafo+router, CRAG, NL2SQL read-only, 5 write-tools con HITL, memoria
corto plazo + slot-filling y guardrails PII básicos. El frontend funciona pero **aplana
los resultados ricos a texto**: las citas RAG se muestran como un bloque markdown al final
del mensaje (y como no hay renderer de markdown, se ven los asteriscos crudos), los
resultados SQL llegan sólo como prosa (las filas estructuradas se descartan), y la
ConfirmCard sólo muestra `action.summary`.

**Objetivo:** renderizar **inline en el chat** tres artefactos estructurados —
**tabla SQL real**, **citas RAG ricas** y **ConfirmCards por-kind**— con una base de
estilos **Tailwind + preset a-ui**. Con esto Fase 1 queda **cerrada**.

## 2. Decisiones (tomadas en brainstorming)

1. **Layout: inline en el chat.** Los artefactos se renderizan DENTRO del flujo de
   mensajes del asistente (estilo ChatGPT/Claude). Sin panel canvas dedicado, sin estado
   de "qué artefacto se muestra".
2. **Alcance de artefactos:** tabla SQL (real, con evento SSE nuevo) + citas RAG ricas +
   ConfirmCards ricas. **Fichas de cliente: diferidas** (hoy no hay flujo backend que las
   produzca).
3. **Estilos: Tailwind vía el preset `@assistant-ui/react/tailwindcss`** (+ PostCSS),
   minimalista (no volverlo un pozo).
4. **Tabla read-only** en este slice (sin sort/filter/export).
5. **Citas informativas** (número + título + página); sin visor de documentos (necesitaría
   backend nuevo).

## 3. No-goals (fuera de alcance de este slice)

- Fichas de cliente (su propio slice / Fase 2).
- Sort / filter / export / paginación de la tabla.
- Visor/preview de documentos (endpoint de contenido por doc/página = backend nuevo).
- Render de markdown en la prosa (`@assistant-ui/react-markdown`): con las citas/tablas ya
  estructuradas, la prosa restante es texto plano → se posterga.
- Panel canvas dedicado (blueprint) — se eligió inline.
- `before → after` en la card de `update_client` (requiere agregar `before` a params) — fast-follow.
- Endurecer guardrails, DSPy, eval-gate, Phoenix, memoria largo plazo = Fase 2.

## 4. Arquitectura de render (el "cómo" inline)

**Mecanismo (confirmado contra los tipos de `@assistant-ui/react@0.7.91`):** cada artefacto
es un **content-part de tipo `tool-call`** que el runtime adapter emite, renderizado por un
**Tool UI registrado**.

- `ThreadConfig` (props de `<Thread>`) acepta `tools?: AssistantToolUI[]`,
  `assistantMessage.components.{Text,ToolFallback}`, `strings` (i18n) y `welcome`.
- `MessagePrimitive.Content` mapea partes a componentes: `Text`, `UI`, y
  `tools.by_name: Record<string, ToolCallContentPartComponent>`.
- Content-parts disponibles en 0.7.91: `text`, `reasoning`, `image`, `file`, `UI`,
  `tool-call`. **No existe** un content-part `source` nativo → las citas van como `tool-call`.
- Los Tool UI se crean con `makeAssistantToolUI({ toolName, render })` y se registran vía
  `<Thread tools={[...]}>` (o `useAssistantToolUI`).

**Mapeo de artefactos → tool UIs:**

| Artefacto | `toolName` | Componente |
|---|---|---|
| Tabla SQL | `praxia_sql_table` | `SqlTable` |
| Citas RAG | `praxia_sources` | `Citations` |
| Confirmación HITL | `praxia_confirm` | `ConfirmCard` (inline) |

**Alternativas descartadas:** (a) rehacer `<Thread>` con primitivas y pasar `components`
explícitos — más código sin beneficio, el `tools` prop alcanza; (b) quedarse en markdown
plano (tabla/citas como markdown) — no da la tabla estructurada que se eligió.

## 5. Contratos de datos

### 5.1 Eventos SSE (backend → frontend)

Existentes (sin cambios): `token` (data: texto crudo), `sources` (data: JSON `Source[]`),
`confirm` (data: `{thread_id, action}`), `done` (data: `[DONE]`).

**NUEVO — `table`:**

```
event: table
data: {"columns": ["cliente","fecha"], "rows": [{"cliente":"Ana","fecha":"2026-07-10T10:00:00+00:00"}], "sql": "SELECT ..."}
```

- `columns`: `string[]` en orden de proyección (de `SqlResult.columns`).
- `rows`: `object[]` (de `SqlResult.rows`).
- `sql`: `string` (el `SELECT` validado; para un toggle "ver consulta").
- **Serialización:** `json.dumps(payload, ensure_ascii=False, default=str)` — obligatorio:
  `run_select` devuelve tipos nativos de asyncpg (datetime/Decimal/date/UUID) que **no** son
  JSON-serializables por defecto; `default=str` los pasa a string (datetime→ISO, etc.).

### 5.2 Content-parts (runtime reducer, frontend)

Reducer PURO `(state, event) → state` extraído a una función testeable. Estado =
`{ text: string, parts: ToolCallPart[] }`. Contenido emitido = `[textPart?] ++ parts`
(texto SIEMPRE primero; luego los artefactos en orden de llegada).

- `token` → acumula en `text`.
- `table` → push `{ type:"tool-call", toolCallId, toolName:"praxia_sql_table", args:{}, result:{columns,rows,sql} }`.
- `sources` → push `{ ..., toolName:"praxia_sources", args:{}, result:{sources} }` (reemplaza el aplanado a markdown).
- `confirm` → push `{ ..., toolName:"praxia_confirm", args:{threadId, action} }`.
- `toolCallId`: `crypto.randomUUID()` por parte.
- La forma exacta del objeto tool-call que espera `useLocalRuntime` se confirma en el
  walking-skeleton (§10 riesgo 1); el shape de arriba se deriva de `ToolCallContentPart`.

### 5.3 `sources` (existente)

`Source = { n: number, title: string, page: number|null, document_id: string }`.
`Citations` renderiza `[n] título — p.{page}` (sin página si `page==null`). `document_id` no
se usa en este slice (linkear al doc = fast-follow). `doc_type` no está en el payload
(agregarlo = fast-follow).

### 5.4 `proposed_action.params` por kind (para ConfirmCard)

La card **selecciona campos por-kind** desde `params` (NO vuelca todo; oculta IDs internos
`client_id`/`practitioner_id`/`appointment_id`). Fechas: `dd/mm HH:MM (UTC)`.

| kind | Título | Campos a mostrar (de `params`) |
|---|---|---|
| `create_appointment` | Agendar turno | Cliente=`client_name`, Profesional=`practitioner_name`, Cuándo=`start_at`–`end_at`, Motivo=`reason`?, Canal=`channel`? |
| `reschedule_appointment` | Reprogramar turno | Cliente=`client_name`, Profesional=`practitioner_name`, De=`old_start_at` → A=`new_start_at` |
| `cancel_appointment` | Cancelar turno (destructivo) | Cliente=`client_name`, Profesional=`practitioner_name`, Turno=`start_at` |
| `log_interaction` | Registrar interacción | Cliente=`client_name`, Tipo=`type`, Contenido=`content` (crudo/completo, WYSIWYG) |
| `update_client` | Actualizar cliente | Cliente=`client_name`, por cada campo presente entre `phone`/`email`/`status`/`dob`: label→valor nuevo |

- `_FIELD_LABELS` para `update_client`: phone→teléfono, email→email, status→estado, dob→fecha de nacimiento.
- La card mantiene `action.summary` como *fallback* de encabezado si un kind fuera desconocido.

## 6. Cambios en el backend (toque chico, full-stack)

Todos en `backend/app/`. No tocan HITL, RAG, router, resolvers, ni los `propose_*`.

### 6.1 `graph/nodes.py`
- Nuevo helper `write_table(columns: list[str], rows: list[dict], sql: str) -> None` que
  emite `get_stream_writer()({"kind":"table","columns":..,"rows":..,"sql":..})`.
- `sql_node` (rama no-abstenida):
  - `answer = await synthesize_sql_answer(question, rows, columns)` (prosa breve, sin tabla).
  - Si **tabular** (`rows` y NO escalar `len(rows)==1 and len(columns)==1`) →
    `write_table(result.columns, result.rows, result.sql or "")`.
  - Stream de los tokens de `answer` (igual que hoy), luego el evento `table` (orden:
    texto primero, tabla después).
  - Escalar / vacío / abstenido → **sin** evento `table` (comportamiento actual).

### 6.2 `main.py`
- `_sse_event_stream`: manejar `kind == "table"` →
  `yield {"event":"table", "data": json.dumps({"columns":..,"rows":..,"sql":..}, ensure_ascii=False, default=str)}`.

### 6.3 `agents/sql_present.py`
- `SYNTH_SYSTEM`: instruir **NO incluir tablas** ("la tabla se muestra por separado;
  resumí en UNA frase breve").
- `_deterministic`: para el caso tabular devolver una frase (p. ej.
  `f"Encontré {len(rows)} resultado(s)."`) en vez de `render_rows_markdown`. El caso escalar
  (`1×1`) sigue devolviendo `"Resultado: {valor}"`.
- `render_rows_markdown` se **mantiene** como serializador interno de las filas para el prompt
  del LLM (se sigue pasando en "Datos:\n{table}"). El guard `_grounded` se mantiene.

## 7. Cambios en el frontend

### 7.1 Tailwind (setup minimalista)
- Dev-deps: `tailwindcss`, `postcss`, `autoprefixer`.
- `tailwind.config.ts`: `content` con los globs de `app/`+`components/`; `plugins:
  [auiPlugin({ components:["default-theme"] })]` (el preset genera los estilos de a-ui).
- `postcss.config.mjs`: `{ plugins: { tailwindcss:{}, autoprefixer:{} } }`.
- `app/globals.css`: `@tailwind base; @tailwind components; @tailwind utilities;`.
- `app/layout.tsx`: **quitar** el import de `@assistant-ui/react/styles/index.css` (lo
  reemplaza el plugin). Verificar `next build` sin flash de estilos.
- Convertir `app/page.tsx` (layout grid/sidebar) a clases Tailwind. Diff acotado.

### 7.2 `lib/chatStream.ts`
- Agregar el evento `table`: `{ type:"table", table:{ columns:string[], rows:Record<string,unknown>[], sql:string } }`.
- `parseEvent`: `if (event === "table") return { type:"table", table: JSON.parse(data) }`.

### 7.3 `lib/runtime.ts`
- Extraer el reducer PURO `event → content-parts` (§5.2) a una función exportada y testeable.
- El adapter deja de aplanar `sources` a markdown (`sourcesBlock` se elimina) y deja de usar
  el callback `onConfirm` (el confirm ahora es inline).
- Emitir content = `[textPart?] ++ toolCallParts`.

### 7.4 Componentes (nuevos / refactor)
- `components/SqlTable.tsx` (nuevo): tabla con header sticky, zebra, scroll horizontal,
  empty-state, read-only; toggle colapsable "ver consulta" que muestra `sql`. Recibe
  `{columns, rows, sql}`.
- `components/Citations.tsx` (nuevo): lista de footnotes numeradas `[n] título — p.{page}`;
  informativas (no clickeables). Recibe `{sources}`.
- `components/ConfirmCard.tsx` (refactor): render **por-kind** (§5.4) de campos legibles;
  mantiene botones Confirmar/Cancelar + streaming del recibo vía `resumeChat`; estilo
  destructivo para `cancel_appointment`. Recibe `{ threadId, action }` (desde `args` del
  tool-call). Mantiene su estado local (idle/working/done).
- `components/toolUIs.tsx` (nuevo): `makeAssistantToolUI` para los tres toolNames, mapeando
  a los componentes de arriba.

### 7.5 `app/page.tsx`
- `<Thread tools={[SqlTableToolUI, CitationsToolUI, ConfirmToolUI]} strings={<es>} welcome={<es>} />`.
- Eliminar el estado `pending` + `onConfirm` + el `<ConfirmCard>` fuera-de-flujo (ahora inline).
- Sidebar (DropZone + DocumentList) se mantiene.

## 8. Testing (gates de DoD)

### 8.1 Frontend (vitest + testing-library)
- `SqlTable`: renderiza columnas/filas en orden; empty-state; toggle de SQL.
- `Citations`: renderiza N fuentes numeradas; oculta " — p." si `page==null`.
- `ConfirmCard`: para cada kind muestra los campos correctos y **oculta IDs**; Confirmar y
  Cancelar disparan `resumeChat` con la decisión correcta; muestra el recibo.
- `chatStream`: parsea el evento `table`.
- `runtime`: el reducer `event→parts` mapea token/table/sources/confirm y respeta el orden
  (texto primero).

### 8.2 Backend (pytest, no-llm)
- `sql_node`: emite `table` en caso tabular; **no** lo emite en escalar/vacío/abstenido
  (mockeando `answer_structured`/`synthesize_sql_answer`).
- `main._sse_event_stream`: reenvía `kind:"table"` como `event:table` y serializa
  datetime/Decimal con `default=str` (no explota).
- `sql_present`: `_deterministic` tabular devuelve frase (no markdown); escalar "Resultado:";
  `synthesize_sql_answer` no emite tabla. Actualizar los tests existentes de `sql_present`.
- El gate no-llm completo no regresiona.

### 8.3 Smoke navegador (frontend/SMOKE.md)
- Consulta SQL (`"¿cuántos turnos esta semana?"` / una que devuelva varias filas) → **tabla**.
- Consulta documental → respuesta + **citas** numeradas.
- Una escritura (agendar/cancelar/…) → **ConfirmCard rica** que **sigue pidiendo
  confirmación** (HITL intacto); confirmar muestra el recibo.
- Actualizar SMOKE.md con los nuevos checks.

## 9. Definition of Done

1. `ruff format` → `ruff check` → `mypy --config-file backend/pyproject.toml` limpios.
2. Backend: gate no-llm no regresiona; nuevos tests de §8.2 pasan.
3. Frontend: `npm run test`, `npm run lint`, `npm run build` pasan; nuevos tests de §8.1.
4. Smoke §8.3 OK; **las escrituras siguen abriendo tarjeta de confirmación** (HITL airtight).
5. Local-first/$0 intacto: Tailwind es tooling de build (dev-time); **cero red saliente
   nueva** del producto en runtime.
6. Commit(s) limpios, sin atribución a Claude.

## 10. Riesgos y mitigaciones

1. **`useLocalRuntime` + adapter emitiendo tool-call parts con `result` precargado** puede
   no renderizar como se espera (el runtime local normalmente asocia results a ejecuciones de
   tool). → **Walking skeleton primero**: Tailwind + un tool-call hardcodeado que renderice
   inline vía `<Thread tools>`. De-riesga TODO lo demás. Si el shape no encaja, fallback:
   `MessagePrimitive.Content` con `tools.by_name` explícito, o un `UI` content-part.
2. **Config Tailwind + plugin a-ui en Next 15 (App Router):** globs de `content` y PostCSS
   correctos; evitar flash de estilos. → seguir docs de a-ui; verificar `build`.
3. **Refactor confirm-inline debe preservar el interrupt/HITL.** El backend no cambia; la
   card sigue llamando `/chat/resume`. → test de ConfirmCard + smoke.
4. **Doble render prosa+tabla en SQL.** → `sql_present` deja de emitir tablas markdown; la
   tabla es el único artefacto tabular.
5. **Serialización del evento `table`** (datetime/Decimal/UUID). → `default=str` + test.

## 11. Fast-follows (no bloquean)

- Fichas de cliente (slice propio): flujo backend + card.
- `before → after` en la card de `update_client` (agregar `before` a params + `default=str`
  en el evento `confirm`).
- Citas clickeables → resaltar el doc en el sidebar (usa `document_id`); `doc_type` en el
  payload de `sources`; visor de documentos.
- Tabla: sort/filter/export/paginación.
- Markdown en prosa (`@assistant-ui/react-markdown`).
- Los fast-follows de backend ya fichados (juez intención↔SQL, UTC en reschedule/cancel,
  denylist SQL, audit log, etc.).

## 12. Secuencia sugerida (para writing-plans)

1. **Walking skeleton:** Tailwind setup + render de UN tool-call hardcodeado inline (de-riesga §10.1).
2. **Backend:** evento `table` (`nodes` + `main`) + política de prosa (`sql_present`) + tests §8.2.
3. **Frontend plumbing:** `chatStream` (evento `table`) + reducer `event→parts` (puro, testeado).
4. **SqlTable** + tool UI `praxia_sql_table`.
5. **Citations** + tool UI `praxia_sources` (reemplaza el aplanado markdown).
6. **ConfirmCard** refactor per-kind + tool UI `praxia_confirm` inline (quita `pending`/`onConfirm`).
7. **i18n `strings`/`welcome`** + conversión Tailwind de `page.tsx`/`layout.tsx` + pulido.
8. **Gate completo + smoke** + actualizar SMOKE.md.
