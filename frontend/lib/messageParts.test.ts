import { expect, test } from "vitest";
import { initialPartsState, reduceEvent, toContent } from "./messageParts";

test("token events accumulate into text", () => {
  let s = initialPartsState;
  s = reduceEvent(s, { type: "token", text: "Hola" });
  s = reduceEvent(s, { type: "token", text: " mundo" });
  expect(s.text).toBe("Hola mundo");
  expect(s.artifacts).toEqual([]);
});

test("non-empty sources become a praxia_sources artifact", () => {
  const src = [{ n: 1, title: "P", page: 2, document_id: "d1" }];
  const s = reduceEvent(initialPartsState, { type: "sources", sources: src });
  expect(s.artifacts).toEqual([{ toolName: "praxia_sources", data: { sources: src } }]);
});

test("empty sources are ignored", () => {
  const s = reduceEvent(initialPartsState, { type: "sources", sources: [] });
  expect(s.artifacts).toEqual([]);
});

test("done/unknown events leave state unchanged", () => {
  const s = reduceEvent({ text: "x", artifacts: [] }, { type: "done" });
  expect(s).toEqual({ text: "x", artifacts: [] });
});

test("toContent puts text first, then tool-call parts with stable ids", () => {
  let s = initialPartsState;
  s = reduceEvent(s, { type: "token", text: "Según [1]" });
  s = reduceEvent(s, {
    type: "sources",
    sources: [{ n: 1, title: "P", page: 2, document_id: "d1" }],
  });
  const content = toContent(s);
  expect(content[0]).toEqual({ type: "text", text: "Según [1]" });
  expect(content[1]).toMatchObject({
    type: "tool-call",
    toolCallId: "praxia-0",
    toolName: "praxia_sources",
  });
  // Hedge §10.1: cada tool-call lleva args, argsText y result (mismos datos). No borrar de toContent.
  const part = content[1] as { args: unknown; result: unknown; argsText: string };
  const expectedData = { sources: [{ n: 1, title: "P", page: 2, document_id: "d1" }] };
  expect(part.args).toEqual(expectedData);
  expect(part.result).toEqual(expectedData);
  expect(part.argsText).toBe(JSON.stringify(expectedData));
});

test("table events become a praxia_sql_table artifact", () => {
  const table = { columns: ["c"], rows: [{ c: "x" }], sql: "SELECT c FROM t" };
  const s = reduceEvent(initialPartsState, { type: "table", table });
  expect(s.artifacts).toEqual([
    { toolName: "praxia_sql_table", data: { columns: ["c"], rows: [{ c: "x" }], sql: "SELECT c FROM t" } },
  ]);
});

test("toContent omits the text part when there is no text", () => {
  const s = reduceEvent(initialPartsState, {
    type: "sources",
    sources: [{ n: 1, title: "P", page: null, document_id: "d1" }],
  });
  const content = toContent(s);
  expect(content).toHaveLength(1);
  expect(content[0]).toMatchObject({ type: "tool-call", toolCallId: "praxia-0" });
});

test("confirm events become a praxia_confirm artifact", () => {
  const action = { kind: "cancel_appointment", summary: "x", params: { client_name: "Ana" } };
  const s = reduceEvent(initialPartsState, { type: "confirm", threadId: "t1", action });
  expect(s.artifacts).toEqual([
    { toolName: "praxia_confirm", data: { threadId: "t1", action } },
  ]);
});
