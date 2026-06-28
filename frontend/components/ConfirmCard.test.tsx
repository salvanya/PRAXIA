import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as chatStream from "../lib/chatStream";
import { ConfirmCard } from "./ConfirmCard";

afterEach(() => vi.restoreAllMocks());

const action = { kind: "create_appointment", summary: "Crear turno: Ana López", params: {} };

test("renders the summary and confirms via resumeChat, showing the receipt", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Turno creado: Ana López" };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t1" action={action} onClose={vi.fn()} />);

  expect(screen.getByText(/Crear turno: Ana López/)).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));

  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "confirm"));
  await waitFor(() => expect(screen.getByText(/Turno creado/)).toBeTruthy());
});

test("cancel calls resumeChat with cancel", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "Cancelado, no creé el turno." };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t1" action={action} onClose={vi.fn()} />);

  fireEvent.click(screen.getByText("Cancelar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "cancel"));
});

test("renders a log_interaction action summary (kind-agnostic card)", async () => {
  const interaction = {
    kind: "log_interaction",
    summary: "Registrar llamada de Ana López — «confirmó el turno»",
    params: {},
  };
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Interacción registrada: llamada de Ana López" };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t9" action={interaction} onClose={vi.fn()} />);

  expect(screen.getByText(/Registrar llamada de Ana López/)).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t9", "confirm"));
  await waitFor(() => expect(screen.getByText(/Interacción registrada/)).toBeTruthy());
});
