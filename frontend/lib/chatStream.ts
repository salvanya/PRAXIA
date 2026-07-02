export interface Source {
  n: number;
  title: string;
  page: number | null;
  document_id: string;
}

export interface ProposedAction {
  kind: string;
  summary: string;
  params: Record<string, unknown>;
}

export interface SqlTablePayload {
  columns: string[];
  rows: Record<string, unknown>[];
  sql: string;
}

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "table"; table: SqlTablePayload }
  | { type: "confirm"; threadId: string; action: ProposedAction }
  | { type: "done" };

function parseEvent(raw: string): ChatEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  const data = dataLines.join("\n");
  if (event === "token") return { type: "token", text: data };
  if (event === "sources") return { type: "sources", sources: JSON.parse(data) as Source[] };
  if (event === "table") return { type: "table", table: JSON.parse(data) as SqlTablePayload };
  if (event === "confirm") {
    const parsed = JSON.parse(data) as { thread_id: string; action: ProposedAction };
    return { type: "confirm", threadId: parsed.thread_id, action: parsed.action };
  }
  if (event === "done") return { type: "done" };
  return null;
}

async function* streamSSE(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `chat failed: ${res.status}`;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      // cuerpo no-JSON: dejamos el mensaje por defecto
    }
    throw new Error(detail);
  }
  if (!res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // sse_starlette delimits events with \r\n\r\n; tolerate \n\n too.
    let m: RegExpExecArray | null;
    const sep = /\r?\n\r?\n/;
    while ((m = sep.exec(buffer)) !== null) {
      const raw = buffer.slice(0, m.index);
      buffer = buffer.slice(m.index + m[0].length);
      const ev = parseEvent(raw);
      if (ev) yield ev;
    }
  }
}

export function streamChat(
  message: string,
  threadId: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  return streamSSE("/api/chat", { message, thread_id: threadId }, signal);
}

export function resumeChat(
  threadId: string,
  decision: "confirm" | "cancel",
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  return streamSSE("/api/chat/resume", { thread_id: threadId, decision }, signal);
}
