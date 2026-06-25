import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as api from "../lib/api";
import { DropZone } from "./DropZone";

afterEach(() => vi.restoreAllMocks());

test("uploading a file calls ingestDocument and reports indexado", async () => {
  vi.spyOn(api, "ingestDocument").mockResolvedValue({
    document_id: "d1", status: "indexado", n_chunks: 2,
  });
  const onIngested = vi.fn();
  render(<DropZone onIngested={onIngested} />);

  const file = new File(["# P"], "protocolo.md", { type: "text/markdown" });
  const input = screen.getByTestId("file-input") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(api.ingestDocument).toHaveBeenCalledWith(file, "protocolo", "protocolo.md"));
  await waitFor(() => expect(screen.getByText(/indexado/i)).toBeTruthy());
  expect(onIngested).toHaveBeenCalled();
});
