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
