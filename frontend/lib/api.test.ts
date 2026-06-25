import { afterEach, expect, test, vi } from "vitest";
import { ingestDocument, listDocuments } from "./api";

afterEach(() => vi.restoreAllMocks());

test("ingestDocument posts multipart and returns the summary", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ document_id: "d1", status: "indexado", n_chunks: 3 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const file = new File(["# Protocolo"], "protocolo.md", { type: "text/markdown" });
  const out = await ingestDocument(file, "protocolo", "Protocolo");

  expect(out.status).toBe("indexado");
  expect(out.n_chunks).toBe(3);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/ingest");
  expect(init.method).toBe("POST");
  expect(init.body).toBeInstanceOf(FormData);
});

test("ingestDocument throws the backend detail on error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "Tipo no soportado: foto.png" }), { status: 415 }),
  ));
  const file = new File(["x"], "foto.png", { type: "image/png" });
  await expect(ingestDocument(file, "protocolo", "Foto")).rejects.toThrow("Tipo no soportado");
});

test("listDocuments returns the array", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify([{ id: "d1", title: "P", doc_type: "protocolo", status: "indexado", page_count: 1, ingested_at: "2026-06-24T00:00:00Z" }]), { status: 200 }),
  ));
  const docs = await listDocuments();
  expect(docs).toHaveLength(1);
  expect(docs[0].title).toBe("P");
});
