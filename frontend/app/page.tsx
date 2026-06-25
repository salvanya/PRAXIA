"use client";

import { useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";

export default function Home() {
  const runtime = useChatRuntime();
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100vh" }}>
        <aside style={{ padding: 16, borderRight: "1px solid #ddd", overflowY: "auto" }}>
          <h1 style={{ fontSize: 18 }}>Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 style={{ fontSize: 14, marginTop: 16 }}>Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section style={{ height: "100vh" }}>
          <Thread />
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
