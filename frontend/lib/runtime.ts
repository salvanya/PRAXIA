"use client";

import {
  useLocalRuntime,
  type ChatModelAdapter,
  type ChatModelRunOptions,
  type ChatModelRunResult,
} from "@assistant-ui/react";
import type { ThreadUserMessage } from "@assistant-ui/react";
import { streamChat, type Source } from "./chatStream";

function lastUserText(messages: ChatModelRunOptions["messages"]): string {
  // Traverse from the end to find the last user message
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const userMsg = msg as ThreadUserMessage;
      return userMsg.content
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("");
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

const adapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }: ChatModelRunOptions): AsyncGenerator<ChatModelRunResult, void> {
    const query = lastUserText(messages);
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

    // Final yield: accumulated answer + sources block
    yield { content: [{ type: "text", text: answer + sourcesBlock(sources) }] };
  },
};

export function useChatRuntime() {
  return useLocalRuntime(adapter);
}
