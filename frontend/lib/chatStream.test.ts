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

test("streamChat parses CRLF line endings (real sse_starlette transport)", async () => {
  // sse_starlette separates events with \r\n\r\n, not \n\n. The browser must
  // handle that or the message renders empty.
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
    "event: token\r\ndata: Ho",
    "la\r\n\r\nevent: token\r\ndata:  mundo\r\n\r\n",
    'event: sources\r\ndata: [{"n":1,"title":"Protocolo","page":2,"document_id":"d1"}]\r\n\r\n',
    "event: done\r\ndata: [DONE]\r\n\r\n",
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
