"use client";

import { useEffect, useState } from "react";
import { listDocuments, type DocumentRow } from "../lib/api";

export function DocumentList({ refreshKey }: { refreshKey: number }) {
  const [docs, setDocs] = useState<DocumentRow[]>([]);

  useEffect(() => {
    let active = true;
    listDocuments()
      .then((d) => { if (active) setDocs(d); })
      .catch(() => { if (active) setDocs([]); });
    return () => { active = false; };
  }, [refreshKey]);

  if (docs.length === 0) return <p style={{ fontSize: 14, color: "#666" }}>Sin documentos aún.</p>;
  return (
    <ul style={{ fontSize: 14, paddingLeft: 18 }}>
      {docs.map((d) => (
        <li key={d.id}>{d.title} — <em>{d.status}</em></li>
      ))}
    </ul>
  );
}
