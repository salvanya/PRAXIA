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
  for (const line of raw.split(/\r?\n/)) {
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
