# Suite de eval offline (gate de Fase 2)

Corre el golden set end-to-end por el grafo real y decide pass/fail:
- **Aserciones deterministas por-caso** (gate duro): intent, citas/abstención, `must_include`,
  y execution-accuracy del SQL (result-set gold vs candidato).
- **Métricas por LLM-as-judge local** (faithfulness / answer_relevancy / context_precision /
  context_recall, LM=`gemma4:12b`, reusando `rag/judges.py`) comparadas contra `baseline.json`
  con tolerancia.

## Correr

Requiere `docker compose up -d` + Ollama (`gemma4:12b` y `gemma4:e4b`).

El gate auto-siembra su propio corpus RAG mínimo (`fixtures.py → ensure_rag_fixture`) antes de
correr los casos. `seed_demo.py` sigue siendo necesario para los datos relacionales (turnos,
clientes, practitioners) que usan los casos SQL; el corpus RAG ya no requiere un seed manual previo.

```bash
cd backend
.venv/Scripts/python -m app.eval.run                 # corre el gate; exit 0/1
.venv/Scripts/python -m app.eval.run --update-baseline  # fija/actualiza baseline.json (commitealo)
.venv/Scripts/python -m app.eval.run --only sql         # solo casos SQL
.venv/Scripts/python -m app.eval.run --tolerance 0.1    # tolerancia del baseline-diff
```

O como test: `python -m pytest backend/tests -m eval -q`.

## Archivos
- `golden_set.jsonl` — casos (versionado; crece con cada bug arreglado).
- `baseline.json` — línea base de métricas de jueces + execution-accuracy (**se commitea**).
- `last_run.json` — resultado de la última corrida (**gitignored**).
