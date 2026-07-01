# Praxia · Fase 1 · Slice 9 — Guardrails PII (Presidio, español): tag no-destructivo en ingesta + redacción destructiva en escrituras de texto libre

> Diseño aprobado el 2026-07-01. Spec de un único slice implementable (el plan lo descompone en tasks incrementales).
> Contrato operativo: `CLAUDE.md` (§0 directiva primaria, §5 guardrails, §6 DoD). Diseño completo del producto: `Praxia_Blueprint.md` (§4.2 guardrails, líneas 297/545/556, §6 Fase 1).
> Slices previos (todos mergeados a `main`): grafo + router (`2026-06-25-grafo-router-design.md`),
> CRAG (`2026-06-26-crag-design.md`), NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`),
> `create_appointment` HITL (`2026-06-27-write-appointment-hitl-design.md`),
> `log_interaction` + registry (`2026-06-28-log-interaction-design.md`),
> `cancel_appointment` (`2026-06-29-cancel-appointment-design.md`),
> `reschedule_appointment` + `update_client` (`2026-06-29-reschedule-and-update-client-design.md`),
> memoria de corto plazo + slot-filling (`2026-06-30-short-term-memory-slot-filling-design.md`).

## ⚠️ Addendum (2026-07-01) — reversión a WYSIWYG en escrituras (post-merge, feedback de usuario)

Tras el smoke en navegador, el usuario decidió **revertir la redacción destructiva del camino de
escritura** (`log_interaction`). La **Decisión de límites #3** (redactar en el proposer; tarjeta ==
lo guardado, ambos redactados) queda **ANULADA**. Motivo: *no se puede confirmar a ciegas* — redactar
las propias notas clínicas del profesional destruye su valor (la fricción que se anticipó en el
brainstorming). Comportamiento final (rama `fase-1/interaction-raw-content`):

- **`log_interaction`**: `summary`/`content` se **muestran y guardan CRUDOS** (WYSIWYG). La tarjeta
  muestra el `content` (el dato real) para verificarlo antes de confirmar. `propose_interaction` ya
  **no** llama a `pii.redact`.
- **La redacción destructiva (`pii.redact`) se reserva para superficies no confiables/compartidas**
  (audit log, exports, docs de terceros) = **Fase 2**. `pii.redact` queda en el módulo, **sin caller
  en Fase 1** (validado por los engine tests). Privacidad at-rest → RLS + cifrado en reposo (Fase 4).
- **Sin cambios:** el módulo `guardrails/pii.py`, el **tag no-destructivo de ingesta**
  (`documents.pii_summary`), el ruteo de notas → `log_interaction`, config, schema, db, y
  `update_client` (siempre mostró/guardó crudo).
- Tests: se quitaron los 2 tests de redacción de `test_interaction_agent.py` (+ fixture identity); el
  e2e `test_guardrails_pii_e2e_llm.py` pasó a asertar **CRUDO** (WYSIWYG) y perdió el marker `pii`.
  Gate no-llm **264**; e2e `-m llm` verde.

> El resto del spec describe el diseño **original** (redacción en escrituras). Leelo con este addendum
> como la **fuente de verdad** del comportamiento final: en escrituras NO se redacta; la redacción es
> solo tag no-destructivo en ingesta (Fase 1) y destructiva en superficies compartidas (Fase 2).

## Objetivo

Cumplir la **directiva primaria §0** (datos de salud, privacidad por defecto) introduciendo la **primera capa de
guardrails de PII** de Praxia, con la semántica correcta para un CRM local de práctica única: **detectar sin
destruir donde la práctica necesita sus propios datos, redactar destructivamente donde el texto es libre y no
estructurado.**

Tres entregables acoplados por un **módulo compartido** (`guardrails/pii.py`, Presidio en español):

1. **Ingesta de documentos — tag no-destructivo.** El pipeline (`POST /ingest` → `ingest_document`) gana un paso
   de **PII scan** tras el parseo: se computa un **resumen de PII** (conteo por tipo) y se guarda en la fila
   `documents` (`pii_summary JSONB`, columna nueva). **El content se embebe intacto** → el RAG sigue recuperando
   los datos del propio paciente sin degradar (blueprint línea 297: *"PII scan (Presidio)"* en el pipeline).
2. **Escrituras de texto libre — redacción destructiva.** `log_interaction` hoy persiste `summary`/`content`
   **crudos** (`interactions.content TEXT`, sin redacción). Se **redacta con placeholders** (`<NOMBRE>`,
   `<TELÉFONO>`, `<EMAIL>`, `<DNI>`, `<CUIT>`, …) el texto libre **en el proposer**, antes de armar
   `proposed_action` → la **ConfirmCard muestra el texto ya redactado** (HITL transparente: el humano confirma
   exactamente lo que se persiste). La identidad del cliente queda intacta (viene del **resolver**/DB, no del
   texto libre).
3. **Notas habilitadas** vía `log_interaction type='nota'`. Hoy `CLASSIFY_PROMPT` rutea *"agregá una nota"* →
   `unsupported` (diferido a Guardrails). Se cambia el ruteo a `log_interaction` (que **ya** soporta
   `type='nota'`); las notas quedan **fechadas, tipadas, queryables** por el SQL agent y se redactan **gratis**
   porque pasan por (2). `clients.notes` (campo blob, semántica *replace*) **no** se usa (ver No-objetivos).

Entregable observable:

- *"registrá que llamé a Juan Pérez, me pasó el DNI 12.345.678 y el tel de la madre 11-2233-4455"* →
  tarjeta **«Registrar llamada de Juan Pérez — «llamé a `<NOMBRE>`, me pasó el DNI `<DNI>` y el tel de la madre
  `<TELÉFONO>`»»** → Confirmar → la fila `interactions` guarda el **content redactado** (sin PII cruda);
  el vínculo al cliente vive en `client_id` + `client_name` (intacto).
- *"agregá una nota sobre Ana: su obra social nueva es OSDE 210-…"* → rutea a `log_interaction` (`type='nota'`) →
  tarjeta con el número de credencial redactado → Confirmar → nota fechada en `interactions`.
- Subo un PDF con datos de pacientes → `documents.pii_summary = {"PERSON": 12, "PHONE": 3, "DNI": 5}`; el RAG
  sigue respondiendo con citas y **con** los nombres reales (content intacto).
- Flujos previos **no regresionan**: turnos, cancel/reschedule, update_client (phone/email/status/dob),
  slot-filling cliente→turno, memoria de corto plazo, RAG/SQL/chitchat. Las escrituras **siguen** pidiendo
  confirmación.

Gate que cierra el slice (CLAUDE.md §2/§6): el smoke registra una interacción con PII → la tarjeta y la fila
persistida están **redactadas**; una nota rutea a `log_interaction` redactada; un doc ingerido puebla
`pii_summary` sin romper el retrieval; **ninguna escritura ocurre sin confirmación**; los 244 no-llm siguen
verdes.

## No-objetivos (diferidos, cada uno trabajo propio)

- **Redacción del mensaje in-flight / checkpointer conversacional** (redactar el `HumanMessage` en la entrada del
  grafo antes del router/LLM/checkpointer): **descartado** en el brainstorming (rompe la extracción de las
  write-tools y el slot-filling, que re-leen el texto crudo; y con LLM local aporta poco). El historial del
  checkpointer sigue conteniendo PII conversacional cruda — **limitación aceptada**, es dato del propio usuario en
  su thread aislado. Redacción en entrada = Fase 2 (Context Manager / split raw-redactado).
- **Redacción de `agent_runs` / audit / trazas Phoenix**: la tabla `agent_runs` **no existe** en el schema
  implementado (es del blueprint; Fase 2 *"audit log completo"*, línea 556). No hay a qué redactar hoy. Cuando se
  agregue, la redacción destructiva reusa `pii.redact`. Fase 2.
- **Detección de inyección de prompt** (`llm-guard`, tratar el contenido de docs subidos como no confiable /
  instrucciones): blueprint línea 556, **Fase 2** (*"guardrails endurecidos"*). Este slice es **solo PII**. El
  `scope_reject` del router y la salida estructurada / SQL read-only ya existen (Slices 1–3).
- **`clients.notes` como campo de ficha** (blob de texto libre, semántica *replace*): descartado a favor de
  `log_interaction type='nota'` (append, fechado, queryable). Para atributos persistentes de ficha
  ("alérgico a X") ya existe `clients.tags JSONB` (estructurado). El campo `clients.notes` queda **sin usar**
  (sin regresión: ya estaba sin poblar).
- **Allow-list del nombre del cliente resuelto en su propio content** (no redactar el `full_name` del cliente ya
  identificado dentro de su interacción): refinamiento **fichado**, no en este slice. Hoy *"llamé a Juan"* →
  *"llamé a `<NOMBRE>`"* — honesto pero algo lossy (el vínculo vive en `client_id`). Fast-follow.
- **Cifrado en reposo** de PII (blueprint línea 301, *"candidata a cifrado en reposo"*): Fase 4 (hardening).
- **Compilar prompts con DSPy**: no aplica (este slice no toca prompts de alto apalancamiento; el único cambio de
  prompt es +1 línea en `CLASSIFY_PROMPT`). Fase 2.
- **Redacción de PII en respuestas de salida del LLM** (output-side PII): el LLM local responde sobre datos que la
  práctica ya posee; la redacción de salida (*output safety*) es Fase 2 (línea 556). Fuera.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). **No se reinventa la rueda**: la detección/anonimización
de PII la hace **Presidio** (OSS, local) con spaCy español, no regex a mano (salvo reconocedores AR puntuales que
Presidio no trae). Inferencia y procesamiento **100% local**: Presidio corre in-process, sin red (la única red es
bajar el modelo spaCy una vez, permitido por §0). **El grafo sigue siendo la fuente de control**: la redacción se
enchufa **dentro** del camino de escritura ya existente (el proposer, detrás del HITL), sin abrir caminos que
esquiven guardrails. **Semántica híbrida** (decisión del brainstorming): no-destructivo donde la práctica recupera
sus datos (ingesta), destructivo donde el texto es libre (escrituras). **Fail-closed** en las escrituras: si la
redacción se espera pero el motor no está disponible, se **abstiene** (nunca se persiste PII cruda por defecto).
Aislamiento por `practice_id` intacto (el `pii_summary` y las escrituras ya viajan scoped).

## Arquitectura

### Decisión de límites #1 — módulo compartido `guardrails/pii.py` (Presidio wrapper; API sync pura + wrap async en los llamadores)

Carpeta nueva `backend/app/guardrails/` (hoy no existe; el mapa de CLAUDE.md §3 ya la reserva con `pii.py`,
`injection.py`, `structured.py` — este slice crea **solo `pii.py`** + `__init__.py`; injection/structured son
Fase 2). El módulo envuelve Presidio y expone **tres funciones puras y testeables**:

```python
class PiiUnavailable(RuntimeError): ...        # el motor no cargó (modelo spaCy ausente, import falló)

def analyze(text: str) -> list[RecognizerResult]     # spans crudos (base de las otras dos)
def summarize(text: str) -> dict[str, int]           # {"PERSON": 3, "PHONE_NUMBER": 1, "AR_DNI": 2}  (ingesta)
def redact(text: str) -> tuple[str, dict[str, int]]  # ("… <NOMBRE> …", {"PERSON": 1})               (escritura)
```

- **Engines Presidio:** `AnalyzerEngine` (con `NlpEngine` español: spaCy `es_core_news_md`) + `AnonymizerEngine`.
  **Singleton lazy** (los engines son pesados: se inicializan una vez, la primera llamada; init falla →
  `PiiUnavailable`). Umbral de score configurable (`PII_SCORE_THRESHOLD`, default 0.5).
- **Reconocedores:** built-in relevantes en ES (`PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER` con región AR/ES,
  `LOCATION`, `CREDIT_CARD`, `IBAN_CODE`) + **custom `PatternRecognizer` argentinos**: `AR_DNI` y `AR_CUIT`
  (regex + context words "DNI"/"documento"/"CUIT"/"CUIL" para subir el score). Se **excluye `DATE_TIME`**
  (las fechas se necesitan y no son PII sensible en este dominio).
- **Placeholders español** (mapa entity_type → etiqueta, usado por el `AnonymizerEngine` con operador `replace`):
  `PERSON→<NOMBRE>`, `PHONE_NUMBER→<TELÉFONO>`, `EMAIL_ADDRESS→<EMAIL>`, `AR_DNI→<DNI>`, `AR_CUIT→<CUIT>`,
  `LOCATION→<UBICACIÓN>`, `CREDIT_CARD→<TARJETA>`, `IBAN_CODE→<IBAN>`, default `<DATO>`.
- **Sync a propósito** (Presidio es CPU-bound sync): las funciones son sync y **testeables sin async**; los
  llamadores en contexto async las envuelven en `await asyncio.to_thread(pii.redact, text)` para no bloquear el
  event loop.
- **Opt-out explícito:** si `PII_REDACTION_ENABLED=false` (dev), `redact` devuelve el texto **sin tocar** (con un
  warning ruidoso una vez) y `summarize` devuelve `{}`; no se instancian engines. Default `true`.
- **Tipos:** Presidio puede no traer stubs completos → `pii.py` usa `# type: ignore` puntual en los imports/áreas
  necesarias (patrón ya usado en el repo), sin relajar `mypy` global.

### Decisión de límites #2 — semántica híbrida (recap del brainstorming): no-destructivo en ingesta, destructivo en escrituras

| Superficie | Modo | Qué se guarda |
|---|---|---|
| Ingesta de docs (`documents`) | **No-destructivo** (`summarize`) | content intacto en Qdrant/DB; `pii_summary` (conteos) en la fila |
| `log_interaction.summary`/`content` (incl. notas) | **Destructivo** (`redact`) | texto con placeholders; `client_id`/`client_name` intactos |
| `agent_runs` / audit | (diferido) | Fase 2 (tabla inexistente) |

### Decisión de límites #3 — la redacción vive en el **proposer**, no en el writer (HITL transparente)

`propose_interaction` redacta `summary`+`content` **después** de resolver el cliente y **antes** de construir
`proposed_action`. Consecuencia clave: la **ConfirmCard refleja lo que se va a persistir** (no hay mismatch
confirm↔store). Si la redacción viviera en `_write_interaction` (writer), la tarjeta mostraría PII cruda y la DB
guardaría redactado → opaco y contra el espíritu del HITL. El **writer no cambia** (persiste lo que ya viene en
`params`, redactado). Orden dentro del proposer: **resolver cliente (texto crudo) → redactar texto libre →
armar params**. El `client_name` de `params` sale del **resolver** (DB), nunca del texto redactado.

### Decisión de límites #4 — ingesta: tag no-destructivo → `documents.pii_summary` (migración aditiva)

`ingest/pipeline.py` gana, **tras el parseo** (texto completo disponible) y sin tocar el content que va al
chunker/embeddings, un `pii_summary = await asyncio.to_thread(pii.summarize, text)`. Se persiste en la fila
`documents`. Migración **aditiva e idempotente** (patrón ya usado con `content_hash`):
`ALTER TABLE documents ADD COLUMN IF NOT EXISTS pii_summary JSONB`. El retrieval **no cambia** (content intacto)
→ la suite offline de eval de RAG **no** se ve afectada.

### Decisión de límites #5 — notas vía `log_interaction type='nota'` (solo `CLASSIFY_PROMPT`), sin tocar `clients.notes`

`write_tools.CLASSIFY_PROMPT`: se **saca** "agregar/editar una NOTA o texto libre de un cliente" de la descripción
de `unsupported` y se **agrega** como ejemplo de `log_interaction` (*"agregá una nota sobre Juan"* → registra una
interacción `type='nota'`). `interaction_agent` **ya** infiere `type='nota'` de su system prompt y pone el texto
en `content` → sin cambios en el agente salvo la redacción de #3. `clients.notes` queda sin usar (No-objetivo).

### Decisión de límites #6 — fail-mode: fail-closed en escrituras, fail-open en ingesta; config `PII_*`

- **Escrituras destructivas (`propose_interaction`):** con `PII_REDACTION_ENABLED=true`, si `pii.redact` lanza
  `PiiUnavailable` (motor no cargó) → **fail-closed**: `ProposalResult(abstained=True, message=<cordial>,
  reason="pii_unavailable")` → no abre tarjeta, no escribe. Nunca se persiste PII cruda cuando se esperaba
  redactar. (Mismo patrón fail-closed del router/`classify_write_action`.) Con `PII_REDACTION_ENABLED=false`,
  `redact` no lanza (devuelve crudo, warning) → la escritura procede en modo dev.
- **Ingesta (tag):** metadata no-crítica → **fail-open con warning** (`pii_summary=None`, la ingesta del documento
  **no** se bloquea por el tag). No tiene sentido rechazar un documento por no poder contar su PII.
- **Config nueva** (`app/config.py`, `.env.example`): `PII_REDACTION_ENABLED: bool = True`,
  `PII_SPACY_MODEL: str = "es_core_news_md"`, `PII_SCORE_THRESHOLD: float = 0.5`. Sin API keys (todo local).

### Decisión de límites #7 — mapa de archivos

```
backend/app/
├── guardrails/
│   ├── __init__.py          # NUEVO (vacío o re-export)
│   └── pii.py               # NUEVO: analyze/summarize/redact + PiiUnavailable + engines lazy + recognizers AR + placeholders
├── ingest/
│   └── pipeline.py          # +paso pii.summarize tras parse; persistir pii_summary en documents (fail-open)
├── agents/
│   ├── interaction_agent.py # redacta summary+content en el proposer (D#3); fail-closed si PiiUnavailable (D#6)
│   └── write_tools.py       # CLASSIFY_PROMPT: "nota" → log_interaction (D#5); WRITE_KINDS sin cambios
├── schema.sql               # ALTER TABLE documents ADD COLUMN IF NOT EXISTS pii_summary JSONB
├── db.py                    # ingest persiste pii_summary (o el pipeline lo hace por su vía actual de insert)
├── config.py                # +PII_REDACTION_ENABLED, PII_SPACY_MODEL, PII_SCORE_THRESHOLD
└── main.py                  # (sin cambios de contrato; /ingest ya existe)
backend/
├── requirements.txt         # +presidio-analyzer, +presidio-anonymizer (spaCy entra por dependencia)
└── (setup) CLAUDE.md §2 + README: `python -m spacy download es_core_news_md` (una vez)
```

Regla CLAUDE.md §3: la lógica de PII vive en `guardrails/pii.py` (un propósito, testeable en aislamiento); los
nodos/agents solo la **invocan**. No se incrusta Presidio en un nodo del grafo.

## Flujo de datos

### Escritura con redacción (`log_interaction`, incluye notas)

```
"registrá que llamé a Juan Pérez, me pasó el DNI 12.345.678 y el tel de la madre 11-2233-4455"
  router (intent=action) → propose_action → classify_write_action → "log_interaction"
  REGISTRY["log_interaction"].propose(question, practice_id, now):
     _extract (12b) → ProposedInteraction{client_name:"Juan Pérez", type:"llamada",
                        summary:"Llamada con Juan Pérez; pasó DNI y teléfono de la madre",
                        content:"llamé a Juan Pérez, me pasó el DNI 12.345.678 y el tel de la madre 11-2233-4455"}
     resolve_single_client("Juan Pérez")  ← TEXTO CRUDO (necesario para matchear) → client=Juan Pérez (id X)
     ── redacción (D#3) ──  (await asyncio.to_thread)
       red_summary = pii.redact(summary)[0] → "Llamada con <NOMBRE>; pasó DNI y teléfono de la madre"
       red_content = pii.redact(content)[0] → "llamé a <NOMBRE>, me pasó el DNI <DNI> y el tel de la madre <TELÉFONO>"
       (si PiiUnavailable y enabled → abstención fail-closed; fin)
     params = {client_id:X, client_name:"Juan Pérez", type:"llamada",
               summary:red_summary, content:red_content, occurred_at, source:"agente"}
     proposed_action = {kind:"log_interaction",
                        summary:_card_summary("Juan Pérez","llamada",red_summary), params}
  confirm_action → interrupt(proposed_action) ⏸ → SSE `confirm`
     ConfirmCard: «Registrar llamada de Juan Pérez — «Llamada con <NOMBRE>; pasó DNI y teléfono…»»
     Confirmar → /chat/resume → db.log_interaction(content=red_content) → fila SIN PII cruda; ✅ recibo
```

- **El cliente se resuelve con el texto crudo** (matchear "Juan Pérez" contra la DB), **después** se redacta el
  texto libre. El `client_name` persistido sale del resolver, no del content.
- Vale para el **path de slot-filling** (override): tras fijar el cliente, el `propose_interaction` re-invocado
  re-extrae del `question` original y **redacta igual** (la redacción no depende del override).

### Ingesta con tag (no-destructivo)

```
POST /ingest (PDF)  → ingest_document(data, filename, doc_type, title):
  parse (docling/unstructured) → text
  pii_summary = pii.summarize(text)  (await to_thread; fail-open → None + warning)   ← NUEVO
  chunk(text) → embed(bge-m3) → upsert Qdrant   ← content INTACTO (no redactado)
  INSERT documents(..., pii_summary=pii_summary, status='indexado')
```

- El content que va a Qdrant y a las citas del RAG es el **original** → el retrieval de datos del propio paciente
  no degrada. `pii_summary` es solo **conteo** (conciencia/consentimiento futuro), no expone spans.

## Módulo `app/guardrails/pii.py` (NUEVO) — detalle

```python
PLACEHOLDERS = {
    "PERSON": "<NOMBRE>", "PHONE_NUMBER": "<TELÉFONO>", "EMAIL_ADDRESS": "<EMAIL>",
    "AR_DNI": "<DNI>", "AR_CUIT": "<CUIT>", "LOCATION": "<UBICACIÓN>",
    "CREDIT_CARD": "<TARJETA>", "IBAN_CODE": "<IBAN>",
}
DEFAULT_PLACEHOLDER = "<DATO>"

class PiiUnavailable(RuntimeError): ...

# Reconocedores AR (PatternRecognizer):
#   AR_DNI  : r"\b\d{1,2}\.?\d{3}\.?\d{3}\b"          context=["dni","documento"]     score≈0.4→boost
#   AR_CUIT : r"\b\d{2}-?\d{8}-?\d\b"                 context=["cuit","cuil"]         score≈0.5→boost
# (regex de arranque; se afinan con los tests para no pisar teléfonos)

@lru_cache(maxsize=1)
def _engines() -> tuple[AnalyzerEngine, AnonymizerEngine]:
    """Init lazy (pesado). Los imports de presidio/spacy van AQUÍ DENTRO (no a nivel de
    módulo) → `import app.guardrails.pii` nunca falla aunque falte presidio/modelo; solo
    falla `_engines()` → PiiUnavailable. Esto mantiene el gate no-llm verde sin el modelo
    (los tests que tocan el motor mockean `pii._engines`/`redact`)."""
    try:
        from presidio_analyzer import AnalyzerEngine, PatternRecognizer  # type: ignore
        from presidio_anonymizer import AnonymizerEngine  # type: ignore
        # ...construir NlpEngine español (settings.pii_spacy_model), registrar AR_DNI/AR_CUIT...
    except Exception as e:  # ImportError / modelo ausente / config
        raise PiiUnavailable(str(e)) from e

def analyze(text: str) -> list["RecognizerResult"]:
    ...  # analyzer.analyze(text, language="es", score_threshold=settings.pii_score_threshold)

def summarize(text: str) -> dict[str, int]:
    if not settings.pii_redaction_enabled: return {}
    counts: dict[str, int] = {}
    for r in analyze(text): counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return counts

def redact(text: str) -> tuple[str, dict[str, int]]:
    if not settings.pii_redaction_enabled:
        _warn_once(); return text, {}
    results = analyze(text)                    # puede lanzar PiiUnavailable (propaga; fail-closed en el caller)
    anonymized = anonymizer.anonymize(text, results, operators={...replace por PLACEHOLDERS...})
    counts = {r.entity_type: ... for r in results}
    return anonymized.text, counts
```

- `settings` = `get_settings()` (patrón del repo). `_warn_once` usa `logging.warning` (no `print`).
- `redact`/`summarize` **sync**; los callers async las envuelven en `asyncio.to_thread`.

## Escritura — `app/agents/interaction_agent.py`

Cambios acotados en `propose_interaction` (el resto del archivo intacto):

```python
from app.guardrails import pii

PII_UNAVAILABLE_MESSAGE = ("No puedo registrar texto libre ahora mismo: el filtro de datos personales "
                           "no está disponible. Avisá al administrador.")

# ...tras resolver `client` (override o resolver), ANTES de armar params:
try:
    red_summary, _ = await asyncio.to_thread(pii.redact, extracted.summary)
    red_content, _ = await asyncio.to_thread(pii.redact, extracted.content)
except pii.PiiUnavailable:
    return ProposalResult(proposed_action=None, abstained=True,
                          message=PII_UNAVAILABLE_MESSAGE, reason="pii_unavailable")

params = {..., "summary": red_summary, "content": red_content, ...}   # client_name intacto (del resolver)
proposed_action = {"kind": "log_interaction",
                   "summary": _card_summary(client["full_name"], extracted.type, red_summary),
                   "params": params}
```

- Nota: `_card_summary` recibe el `red_summary` (la tarjeta muestra el resumen redactado). El `client["full_name"]`
  (estructurado) queda visible en el encabezado de la tarjeta — es el dato que el humano necesita para confirmar
  **de quién** es la interacción.

## Clasificador — `app/agents/write_tools.py` (`CLASSIFY_PROMPT`)

- Línea de `log_interaction`: agregar ejemplo *"agregá una nota sobre Ana"* → `log_interaction` (`type='nota'`).
- Línea de `unsupported`: quitar "agregar/editar una NOTA o texto libre de un cliente"; dejar el resto
  (facturar, borrar registros). `WRITE_KINDS`, `REGISTRY` y `classify_write_action` **sin cambios estructurales**.

## Ingesta — `app/ingest/pipeline.py` (+ `schema.sql`, `db.py`)

- `pipeline.py`: tras obtener el `text` parseado, `pii_summary = await asyncio.to_thread(pii.summarize, text)`
  envuelto en `try/except` (cualquier fallo → `logging.warning` + `pii_summary = None`; fail-open). Se pasa al
  insert de `documents`.
- `schema.sql`: `ALTER TABLE documents ADD COLUMN IF NOT EXISTS pii_summary JSONB;` (idempotente, junto al patrón
  `content_hash`).
- `db.py` / el insert de documents: aceptar y persistir `pii_summary` (JSONB serializable; `None` permitido).

## Config — `app/config.py`

```python
pii_redaction_enabled: bool = True         # opt-out explícito (dev) desactiva la redacción con warning
pii_spacy_model: str = "es_core_news_md"   # lg = upgrade documentado (más preciso, más pesado)
pii_score_threshold: float = 0.5           # umbral de Presidio
```
`.env.example` documenta las tres. Sin API keys (todo local, $0).

## Multi-tenant (CLAUDE.md §0.5)

Sin superficie nueva de fuga. La redacción opera sobre texto **de un solo request/tool** (no cruza prácticas). El
`pii_summary` se guarda en la fila `documents` que ya lleva `practice_id`. Las escrituras (`log_interaction`) ya
re-verifican `practice_id`/`client_id` en el writer (Slices 4–7). El resolver de cliente sigue scoped. RLS = Fase 4.

## Seguridad / guardrails (CLAUDE.md §5)

- **PII (§5):** este slice **implementa** "PII redaction en ingesta" (blueprint 545, como tag no-destructivo) +
  "el audit log de negocio no guarda PII cruda" para `log_interaction` (donde hoy sí la guardaba). Es la primera
  capa concreta de la directiva §0.
- **HITL inquebrantable:** la redacción vive **dentro** del proposer, detrás del `interrupt`. No abre caminos que
  esquiven la confirmación; la tarjeta muestra el texto redactado que se persistirá. El fail-closed (motor caído)
  **abstiene**, no escribe.
- **Fail-closed por defecto:** con la redacción esperada, un fallo del motor no degrada silenciosamente a
  guardar crudo (salvo opt-out explícito de dev). Consistente con el resto del sistema.
- **Contenido de documentos = no confiable (inyección):** este slice **no** aborda inyección (Fase 2). El PII scan
  de ingesta **no** ejecuta instrucciones del doc: solo cuenta entidades. No amplía la superficie de inyección.
- **Sin red saliente:** Presidio + spaCy corren in-process. Única red: bajar `es_core_news_md` una vez (§0).

## Testing (DoD CLAUDE.md §6)

Patrón del repo: inyección de dependencias, `monkeypatch` de funciones de módulo, `MemorySaver` para el ciclo HITL.
**Nuevo marker `pii`** (registrado en `backend/pyproject.toml`, análogo a `llm`) para tests que requieren el modelo
spaCy + engine real → el gate `-m "not llm"` **no** los corre (queda verde en máquinas sin el modelo). Se agrega a
la invocación e2e como `-m "llm or pii"` cuando el entorno los tenga.

- **No-llm** (sin Ollama, sin spaCy; analyzer/redact **mockeados**):
  - `test_pii_module.py` (nuevo, lógica pura): con `PII_REDACTION_ENABLED=false` → `redact` devuelve el texto
    idéntico y `{}`, `summarize` `{}` (sin instanciar engines); con `pii._engines` monkeypatcheado para lanzar →
    `redact` propaga `PiiUnavailable`; el mapa `PLACEHOLDERS` cubre los tipos esperados; los **regex AR** (`AR_DNI`,
    `AR_CUIT`) se testean como `re` crudos (matchean "12.345.678", "20-12345678-3"; **no** matchean un teléfono
    "11-2233-4455" como DNI) — sin Presidio.
  - `test_interaction_agent.py` (extender): con `pii.redact` monkeypatcheado a un fake (mapea "Juan"→"<NOMBRE>",
    "12.345.678"→"<DNI>"): `propose_interaction` pone `params.content`/`params.summary` **redactados** y
    `client_name` **intacto** (del resolver); la `proposed_action.summary` (tarjeta) usa el summary redactado.
    Con `pii.redact` lanzando `PiiUnavailable` y `enabled=true` → `ProposalResult(abstained, reason="pii_unavailable")`
    (no `proposed_action`). Path override (slot-filling) también redacta.
  - `test_write_tools.py` (extender): `classify_write_action("agregá una nota sobre Juan")` → `"log_interaction"`
    (con `llm` fake devolviendo la etiqueta); ya no `"unsupported"`. No-regresión de los otros 5 mapeos.
  - `test_ingest_pipeline.py` (extender/crear): con `pii.summarize` monkeypatcheado → el insert de `documents`
    recibe `pii_summary` poblado; con `pii.summarize` lanzando → `pii_summary=None` y la ingesta **completa**
    (fail-open, sin romper chunk/embed, ambos ya mockeados en los tests de ingesta).
  - `test_config.py` (si existe) / smoke de settings: los tres `pii_*` tienen defaults correctos.
  - **No-regresión:** los 244 no-llm verdes sin tocar asertos; `test_hitl_cycle.py` (5 kinds) intacto; las
    escrituras siguen pidiendo confirmación.
- **`-m pii`** (requiere `es_core_news_md`; sin Ollama):
  - `test_pii_engine_pii.py` (nuevo): `redact("Llamé a Juan Pérez, DNI 12.345.678, mail a@b.com, tel 11-2233-4455")`
    → el output **no** contiene "Juan Pérez" ni "12.345.678" ni "a@b.com"; contiene `<NOMBRE>`, `<DNI>`, `<EMAIL>`,
    `<TELÉFONO>`; `summarize` cuenta ≥1 de cada tipo esperado. `redact` de texto sin PII → texto idéntico.
- **`-m llm`** (Ollama + Postgres + `seed_demo.py`; e2e):
  - `test_guardrails_pii_e2e_llm.py` (nuevo): *"registrá que llamé a \<cliente del seed\>, su DNI es 30.111.222"*
    → tarjeta con `<DNI>` en el resumen; `resume="confirm"` → fila `interactions` cuyo `content` **no** contiene
    "30.111.222" y **sí** `<DNI>`; `client_id` correcto (verificado por id). *"agregá una nota sobre \<cliente\>:
    …"* → `type='nota'` persistida y redactada. No-regresión: un `create_appointment`/`update_client` one-shot
    sigue verde. (Requiere también el modelo spaCy → se corre con `-m "llm or pii"` en el entorno completo.)
- **Gates:** `ruff format` **antes** de `ruff check`; `mypy --config-file backend/pyproject.toml` (el `# type: ignore`
  de Presidio no rompe); `pytest -m "not llm" -q` verde. **Smoke §2 ampliado:** registrar interacción con PII →
  tarjeta y fila redactadas; nota → `log_interaction` redactada; doc con PII → `documents.pii_summary` poblado y el
  RAG sigue citando con datos reales; los flujos de Slices 1–8 no regresionan.

## Dependencias

- **Nuevas (OSS, local, $0):** `presidio-analyzer`, `presidio-anonymizer` (arrastran `spacy`). Modelo spaCy
  `es_core_news_md` vía `python -m spacy download es_core_news_md` (una vez; red permitida por §0 para bajar
  modelos). Documentar en `CLAUDE.md §2` (arranque) y `.env.example` (vars `PII_*`).
- Sin servicios cloud, sin API keys. Sin red saliente en runtime (solo Ollama/PG/Qdrant locales + Presidio
  in-process).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -m "not llm" -q` verdes; `-m pii` verde con el
   modelo spaCy; `-m llm` verde con Ollama + ambos modelos + Postgres + `seed_demo.py`.
2. Tocamos escrituras (proposer) y el pipeline de ingesta: **las escrituras siguen pidiendo confirmación de
   verdad**, la tarjeta muestra el texto **redactado**, la fila persistida no tiene PII cruda, y la ingesta
   completa con `pii_summary`. Smoke §2 ampliado pasa.
3. No se tocó retrieval/SQL/síntesis ni el **prompt del router** → la suite offline de eval de RAG/SQL no aplica
   (el content de ingesta queda intacto → retrieval sin cambios). Si un e2e mostrara fallo de redacción, se agrega
   caso al golden — anotado.
4. Prompts: solo +1 ejemplo en `CLASSIFY_PROMPT` (bajo apalancamiento, a mano). DSPy = Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales (Presidio in-process; el download del modelo es setup, no
   runtime).
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Presidio redacta de más (falsos positivos)** — p. ej. redacta un término clínico como `PERSON`: mitigado por
  el umbral (`PII_SCORE_THRESHOLD`) afinable y por tests `-m pii` con textos representativos. El costo de un falso
  positivo (un `<NOMBRE>` de más en una nota) es bajo y visible en la tarjeta antes de confirmar.
- **Presidio redacta de menos (falsos negativos)** — deja pasar un DNI raro: los reconocedores AR custom + tests
  cubren los formatos comunes; el residual es aceptable para una primera capa (privacidad por defecto, no
  perfección). Fast-follow: afinar recognizers con casos reales.
- **Redacta el nombre del propio cliente en su content** (`<NOMBRE>`): honesto pero lossy; el vínculo vive en
  `client_id`/`client_name`. Refinamiento allow-list fichado (No-objetivo).
- **Modelo spaCy ausente en la máquina** → `PiiUnavailable`: escrituras **fail-closed** (abstención con mensaje
  claro), ingesta **fail-open** (tag `None`). Documentado en setup; `PII_REDACTION_ENABLED=false` como escape de
  dev con warning.
- **Latencia de Presidio en el proposer** (spaCy sync): envuelto en `asyncio.to_thread`; el proposer ya hace una
  llamada LLM (extract) que domina la latencia. Aceptable para un solo usuario dev; vLLM/tuning = Fase 4.
- **`es_core_news_md` vs `lg`:** `md` balancea tamaño/precisión; si el recall de `PERSON` fuera pobre, `lg` es un
  cambio de una var (`PII_SPACY_MODEL`) — documentado, sin código.
- **PII conversacional en el checkpointer** sigue cruda: limitación aceptada (No-objetivo); Fase 2 la aborda con
  redacción en entrada / Context Manager. Este slice **reduce** el problema (las escrituras derivadas ya no la
  propagan a `interactions`).
