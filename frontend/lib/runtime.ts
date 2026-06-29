"use client";

import { useMemo } from "react";
import {
  useLocalRuntime,
  type ChatModelAdapter,
  type ChatModelRunOptions,
  type ChatModelRunResult,
} from "@assistant-ui/react";
import type { ThreadUserMessage } from "@assistant-ui/react";
import { streamChat, type ProposedAction, type Source } from "./chatStream";

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

function sourcesBlock(sources: Source[]): string {
  if (sources.length === 0) return "";
  const lines = sources.map(
    (s) => `[${s.n}] ${s.title}${s.page != null ? ` — p.${s.page}` : ""}`,
  );
  return `\n\n**Fuentes:**\n${lines.join("\n")}`;
}

export function useChatRuntime(onConfirm?: (p: PendingAction) => void) {
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({ messages, abortSignal }: ChatModelRunOptions): AsyncGenerator<ChatModelRunResult, void> {
        const query = lastUserText(messages);
        let answer = "";
        let sources: Source[] = [];

        try {
          for await (const ev of streamChat(query, abortSignal)) {
            if (ev.type === "token") {
              answer += ev.text;
              yield { content: [{ type: "text", text: answer }] };
            } else if (ev.type === "sources") {
              sources = ev.sources;
            } else if (ev.type === "confirm") {
              onConfirm?.({ threadId: ev.threadId, action: ev.action });
              yield {
                content: [{ type: "text", text: "📝 Propuse una acción — revisá la tarjeta de confirmación." }],
              };
              return;
            }
          }
        } catch (err) {
          if (abortSignal?.aborted) return;
          const message = err instanceof Error ? err.message : "No se pudo contactar al asistente.";
          yield { content: [{ type: "text", text: message }] };
          return;
        }

        yield { content: [{ type: "text", text: answer + sourcesBlock(sources) }] };
      },
    }),
    [onConfirm],
  );
  return useLocalRuntime(adapter);
}
