import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import type { ItemDetail, Turn } from "../api/client";
import { ItemSheet } from "./ItemSheet";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  getItem: vi.fn(),
  editItem: vi.fn(),
  listEntities: vi.fn(),
}));

/** Only what the editor reads of a memory; the rest is the server's business. */
const detail = {
  item: {
    id: "i1",
    workspace_id: "w1",
    kind: "note",
    subtype: null,
    title: "Passport in the blue drawer",
    text: "I keep my passport in the blue drawer.",
    summary: null,
    attributes: {},
    tags: [],
    state: "current",
    in_core: false,
    in_quick: true,
    occurred_start: null,
    due_at: null,
  },
  entities: [],
  links: [],
  triggers: [],
  source: null,
} as unknown as ItemDetail;

function turn(fields: Partial<Pick<Turn, "status" | "error">>): Turn {
  return { id: "t1", output: null, status: "completed", error: null, ...fields } as Turn;
}

async function save(result: Turn) {
  vi.mocked(client.getItem).mockResolvedValue(detail);
  vi.mocked(client.listEntities).mockResolvedValue([]);
  vi.mocked(client.editItem).mockResolvedValue(result);
  const onClose = vi.fn();
  const onTurn = vi.fn();
  render(
    <ItemSheet itemId="i1" mode="overlay" timezone="UTC" onClose={onClose} onTurn={onTurn} />,
  );
  fireEvent.change(await screen.findByLabelText("Category"), {
    target: { value: "documents" },
  });
  fireEvent.click(screen.getByTestId("edit-save"));
  await waitFor(() => {
    expect(client.editItem).toHaveBeenCalledTimes(1);
  });
  return { onClose, onTurn };
}

describe("the memory editor's Save", () => {
  it("closes once the edit completed", async () => {
    const { onClose, onTurn } = await save(turn({}));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    expect(onTurn).toHaveBeenCalledTimes(1);
  });

  it("stays open and says why when the turn was stored as failed", async () => {
    const failed = turn({
      status: "failed",
      error: { code: "internal_error", message: "this turn was not saved" },
    });
    const { onClose, onTurn } = await save(failed);
    expect((await screen.findByRole("alert")).textContent).toContain("this turn was not saved");
    expect(onClose).not.toHaveBeenCalled();
    expect(onTurn).toHaveBeenCalledWith(failed);
  });
});
