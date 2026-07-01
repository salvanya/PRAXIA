"use client";

import { useState } from "react";
import { resumeChat, type ProposedAction } from "../lib/chatStream";

export interface CardRow {
  label: string;
  value: string;
}
export interface CardView {
  title: string;
  destructive: boolean;
  rows: CardRow[];
}

const FIELD_LABELS: Record<string, string> = {
  phone: "Teléfono",
  email: "Email",
  status: "Estado",
  dob: "Fecha de nacimiento",
};

function fmtDateTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}/${p(d.getUTCMonth() + 1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

function fmtRange(startIso: string, endIso: string): string {
  if (!startIso) return "";
  const base = fmtDateTime(startIso).replace(" UTC", "");
  if (!endIso) return `${base} UTC`;
  const e = new Date(endIso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${base}–${p(e.getUTCHours())}:${p(e.getUTCMinutes())} UTC`;
}

export function cardFields(action: ProposedAction): CardView {
  const p = action.params as Record<string, unknown>;
  const s = (k: string) => (p[k] == null ? "" : String(p[k]));
  switch (action.kind) {
    case "create_appointment": {
      const rows: CardRow[] = [
        { label: "Cliente", value: s("client_name") },
        { label: "Profesional", value: s("practitioner_name") },
        { label: "Cuándo", value: fmtRange(s("start_at"), s("end_at")) },
      ];
      if (p.reason) rows.push({ label: "Motivo", value: s("reason") });
      if (p.channel) rows.push({ label: "Canal", value: s("channel") });
      return { title: "Agendar turno", destructive: false, rows };
    }
    case "reschedule_appointment":
      return {
        title: "Reprogramar turno",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Profesional", value: s("practitioner_name") },
          { label: "De", value: fmtDateTime(s("old_start_at")) },
          { label: "A", value: fmtDateTime(s("new_start_at")) },
        ],
      };
    case "cancel_appointment":
      return {
        title: "Cancelar turno",
        destructive: true,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Profesional", value: s("practitioner_name") },
          { label: "Turno", value: fmtDateTime(s("start_at")) },
        ],
      };
    case "log_interaction":
      return {
        title: "Registrar interacción",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          { label: "Tipo", value: s("type") },
          { label: "Contenido", value: s("content") },
        ],
      };
    case "update_client": {
      const changed = ["phone", "email", "status", "dob"].filter(
        (k) => p[k] != null && p[k] !== "",
      );
      return {
        title: "Actualizar cliente",
        destructive: false,
        rows: [
          { label: "Cliente", value: s("client_name") },
          ...changed.map((k) => ({ label: FIELD_LABELS[k], value: s(k) })),
        ],
      };
    }
    default:
      return { title: "Confirmar acción", destructive: false, rows: [{ label: "", value: action.summary }] };
  }
}

export function ConfirmCard({ threadId, action }: { threadId: string; action: ProposedAction }) {
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [receipt, setReceipt] = useState("");
  const view = cardFields(action);

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
      className={`my-2 rounded-lg border p-3 ${
        view.destructive ? "border-red-200 bg-red-50" : "border-gray-200 bg-gray-50"
      }`}
    >
      <p className="mb-2 font-semibold text-gray-800">{view.title}</p>
      <dl className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        {view.rows.map((r, i) => (
          <div key={i} className="contents">
            <dt className="text-gray-500">{r.label}</dt>
            <dd className="whitespace-pre-wrap text-gray-800">{r.value}</dd>
          </div>
        ))}
      </dl>
      {phase !== "done" ? (
        <div className="flex gap-2">
          <button
            onClick={() => decide("confirm")}
            disabled={phase === "working"}
            className={`rounded px-3 py-1 text-sm font-medium text-white disabled:opacity-50 ${
              view.destructive ? "bg-red-600" : "bg-blue-600"
            }`}
          >
            Confirmar
          </button>
          <button
            onClick={() => decide("cancel")}
            disabled={phase === "working"}
            className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 disabled:opacity-50"
          >
            Cancelar
          </button>
        </div>
      ) : (
        <p className="whitespace-pre-wrap text-sm text-gray-800">{receipt}</p>
      )}
    </div>
  );
}
