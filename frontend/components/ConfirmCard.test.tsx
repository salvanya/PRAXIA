import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as chatStream from "../lib/chatStream";
import { cardFields, ConfirmCard } from "./ConfirmCard";

afterEach(() => vi.restoreAllMocks());

test("cardFields for create_appointment shows readable fields and hides ids", () => {
  const view = cardFields({
    kind: "create_appointment",
    summary: "x",
    params: {
      client_id: "c1",
      client_name: "Ana López",
      practitioner_id: "p1",
      practitioner_name: "Dra. Gómez",
      start_at: "2026-07-10T10:00:00+00:00",
      end_at: "2026-07-10T10:30:00+00:00",
      reason: "control",
    },
  });
  expect(view.title).toBe("Agendar turno");
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("Cliente");
  expect(labels).toContain("Profesional");
  expect(labels).toContain("Cuándo");
  expect(labels).toContain("Motivo");
  // no exponer ids internos
  const values = view.rows.map((r) => r.value).join(" ");
  expect(values).not.toContain("c1");
  expect(values).not.toContain("p1");
});

test("cardFields for reschedule shows old → new", () => {
  const view = cardFields({
    kind: "reschedule_appointment",
    summary: "x",
    params: {
      appointment_id: "a1",
      client_name: "Ana",
      practitioner_name: "Dr. X",
      old_start_at: "2026-07-10T10:00:00+00:00",
      new_start_at: "2026-07-12T15:00:00+00:00",
    },
  });
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("De");
  expect(labels).toContain("A");
});

test("cardFields for cancel is destructive", () => {
  const view = cardFields({
    kind: "cancel_appointment",
    summary: "x",
    params: { appointment_id: "a1", client_name: "Ana", practitioner_name: "Dr. X", start_at: "2026-07-10T10:00:00+00:00" },
  });
  expect(view.destructive).toBe(true);
});

test("cardFields for update_client lists changed fields with labels", () => {
  const view = cardFields({
    kind: "update_client",
    summary: "x",
    params: { client_id: "c1", client_name: "Ana", phone: "099-123", status: "activo" },
  });
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("Teléfono");
  expect(labels).toContain("Estado");
  expect(view.rows.find((r) => r.label === "Teléfono")?.value).toBe("099-123");
});

test("cardFields for log_interaction shows Cliente, Tipo, Contenido", () => {
  const view = cardFields({
    kind: "log_interaction",
    summary: "x",
    params: { client_name: "Ana", type: "llamada", content: "confirmó el turno" },
  });
  expect(view.title).toBe("Registrar interacción");
  const labels = view.rows.map((r) => r.label);
  expect(labels).toContain("Cliente");
  expect(labels).toContain("Tipo");
  expect(labels).toContain("Contenido");
  expect(view.rows.find((r) => r.label === "Contenido")?.value).toBe("confirmó el turno");
  expect(view.destructive).toBe(false);
});

test("confirm streams the receipt via resumeChat", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Turno creado: Ana López" };
      yield { type: "done" };
    })(),
  );
  render(
    <ConfirmCard
      threadId="t1"
      action={{
        kind: "create_appointment",
        summary: "x",
        params: { client_name: "Ana López", practitioner_name: "Dra. Gómez", start_at: "2026-07-10T10:00:00+00:00", end_at: "2026-07-10T10:30:00+00:00" },
      }}
    />,
  );
  expect(screen.getByText("Agendar turno")).toBeTruthy();
  expect(screen.getByText("Ana López")).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "confirm"));
  await waitFor(() => expect(screen.getByText(/Turno creado/)).toBeTruthy());
});

test("cancel calls resumeChat with cancel", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "Listo, dejé el turno como estaba." };
      yield { type: "done" };
    })(),
  );
  render(
    <ConfirmCard
      threadId="t9"
      action={{ kind: "cancel_appointment", summary: "x", params: { client_name: "Ana", practitioner_name: "Dr. X", start_at: "2026-07-10T10:00:00+00:00" } }}
    />,
  );
  fireEvent.click(screen.getByText("Cancelar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t9", "cancel"));
});
