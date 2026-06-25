"use client";

import { useState } from "react";
import { ingestDocument } from "../lib/api";

export function DropZone({ onIngested }: { onIngested: () => void }) {
  const [status, setStatus] = useState<string>("");

  async function handleFile(file: File) {
    setStatus(`procesando ${file.name}…`);
    try {
      const summary = await ingestDocument(file, "protocolo", file.name);
      setStatus(`${file.name}: ${summary.status} (${summary.n_chunks} fragmentos)`);
      onIngested();
    } catch (e) {
      setStatus(`error: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) void handleFile(file);
      }}
      style={{ border: "2px dashed #999", borderRadius: 8, padding: 24, textAlign: "center" }}
    >
      <p>Soltá un PDF o MD aquí</p>
      <input
        data-testid="file-input"
        type="file"
        accept=".pdf,.md,.markdown,.txt"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      {status && <p style={{ marginTop: 12, fontSize: 14 }}>{status}</p>}
    </div>
  );
}
