"use client";

import { useState } from "react";
import { resumeChat, type ProposedAction } from "../lib/chatStream";

export function ConfirmCard({
  threadId,
  action,
  onClose,
}: {
  threadId: string;
  action: ProposedAction;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [receipt, setReceipt] = useState("");

  async function decide(decision: "confirm" | "cancel") {
    setPhase("working");
    let text = "";
    try {
      for await (const ev of resumeChat(threadId, decision)) {
        if (ev.type === "token") {
          text += ev.text;
          setReceipt(text);
        }
      }
    } catch (err) {
      setReceipt(err instanceof Error ? err.message : "No se pudo completar la acción.");
    }
    setPhase("done");
  }

  return (
    <div
      style={{
        border: "1px solid #c7c7c7",
        borderRadius: 8,
        padding: 12,
        margin: 12,
        background: "#fafafa",
      }}
    >
      <p style={{ fontWeight: 600, margin: "0 0 8px" }}>{action.summary}</p>
      {phase !== "done" ? (
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => decide("confirm")} disabled={phase === "working"}>
            Confirmar
          </button>
          <button onClick={() => decide("cancel")} disabled={phase === "working"}>
            Cancelar
          </button>
        </div>
      ) : (
        <div>
          <p style={{ margin: "0 0 8px" }}>{receipt}</p>
          <button onClick={onClose}>Cerrar</button>
        </div>
      )}
    </div>
  );
}
