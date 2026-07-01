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
    default:
      // confirm se agrega en task 7; done/desconocidos se ignoran
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
      // `result` = mismos datos: marca el tool-call como resuelto (no "requires-action")
      // y da doble cobertura de render en a-ui (los Tool UI leen `args`). Hedge del §10.1.
      result: a.data,
    } as unknown as ThreadAssistantContentPart);
  });
  return parts;
}
