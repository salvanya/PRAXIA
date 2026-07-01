"use client";

import { useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";
import { SourcesToolUI, SqlTableToolUI, ConfirmToolUI } from "../components/toolUIs";

export default function Home() {
  const [refreshKey, setRefreshKey] = useState(0);
  const runtime = useChatRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main className="grid h-screen grid-cols-[320px_1fr]">
        <aside className="overflow-y-auto border-r border-gray-200 p-4">
          <h1 className="text-lg font-semibold">Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 className="mt-4 text-sm font-medium text-gray-700">Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section className="flex h-screen min-h-0 flex-col">
          <div className="min-h-0 flex-1">
            <Thread
              tools={[SourcesToolUI, SqlTableToolUI, ConfirmToolUI]}
              welcome={{
                message:
                  "Hola 👋 Preguntame por tu agenda o tus documentos, o pedime agendar, reprogramar, cancelar, registrar o actualizar datos.",
              }}
              strings={{
                composer: {
                  input: { placeholder: "Escribí tu mensaje…" },
                  send: { tooltip: "Enviar" },
                },
              }}
            />
          </div>
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
