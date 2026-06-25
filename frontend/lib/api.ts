export interface DocumentSummary {
  document_id: string;
  status: string;
  n_chunks: number;
}

export interface DocumentRow {
  id: string;
  title: string;
  doc_type: string;
  status: string;
  page_count: number | null;
  ingested_at: string;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : `Error ${res.status}`;
  } catch {
    return `Error ${res.status}`;
  }
}

export async function ingestDocument(
  file: File,
  docType: string,
  title: string,
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("doc_type", docType);
  form.append("title", title);
  const res = await fetch("/api/ingest", { method: "POST", body: form });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as DocumentSummary;
}

export async function listDocuments(): Promise<DocumentRow[]> {
  const res = await fetch("/api/documents");
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as DocumentRow[];
}
