import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Citations } from "./Citations";

test("renders numbered sources with title and page", () => {
  render(
    <Citations
      sources={[
        { n: 1, title: "Protocolo", page: 2, document_id: "d1" },
        { n: 2, title: "Ficha", page: null, document_id: "d2" },
      ]}
    />,
  );
  expect(screen.getByText("[1]")).toBeTruthy();
  expect(screen.getByText(/Protocolo — p\.2/)).toBeTruthy();
  expect(screen.getByText("[2]")).toBeTruthy();
  // page null → sin " — p."
  expect(screen.getByText("Ficha")).toBeTruthy();
});

test("renders nothing when there are no sources", () => {
  const { container } = render(<Citations sources={[]} />);
  expect(container.firstChild).toBeNull();
});
