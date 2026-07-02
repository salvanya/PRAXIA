import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { SqlTable } from "./SqlTable";

test("renders columns in order and cell values", () => {
  render(
    <SqlTable
      columns={["cliente", "fecha"]}
      rows={[
        { cliente: "Ana", fecha: "10/07" },
        { cliente: "Beto", fecha: "11/07" },
      ]}
    />,
  );
  // orden de columnas, no sólo presencia
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers).toEqual(["cliente", "fecha"]);
  expect(screen.getByText("Ana")).toBeTruthy();
  expect(screen.getByText("Beto")).toBeTruthy();
});

test("shows an empty state when there are no rows", () => {
  render(<SqlTable columns={["cliente"]} rows={[]} />);
  expect(screen.getByText(/Sin resultados/)).toBeTruthy();
});

test("toggles the SQL view", () => {
  render(<SqlTable columns={["c"]} rows={[{ c: "x" }]} sql="SELECT c FROM t" />);
  expect(screen.queryByText("SELECT c FROM t")).toBeNull();
  fireEvent.click(screen.getByText(/ver consulta/));
  expect(screen.getByText("SELECT c FROM t")).toBeTruthy();
});
