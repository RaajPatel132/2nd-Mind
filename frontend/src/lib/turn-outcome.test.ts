import { describe, expect, it } from "vitest";
import type { Turn } from "../api/client";
import { whyNotDone } from "./turn-outcome";

/** Only the fields the helper reads; the rest of a Turn is the server's business. */
function turn(fields: Partial<Pick<Turn, "status" | "output" | "error">>): Turn {
  return { status: "completed", output: null, error: null, ...fields } as Turn;
}

describe("whyNotDone", () => {
  it("is null for a completed turn, whatever it said", () => {
    expect(whyNotDone(turn({ output: "Edited: changed 'x'." }))).toBeNull();
  });

  it("gives a failed turn's own reason", () => {
    const failed = turn({
      status: "failed",
      error: { code: "internal_error", message: "this turn was not saved" },
    });
    expect(whyNotDone(failed)).toBe("this turn was not saved");
  });

  it("falls back to the output, then to a plain sentence", () => {
    expect(whyNotDone(turn({ status: "failed", output: "Stopped early." }))).toBe(
      "Stopped early.",
    );
    expect(whyNotDone(turn({ status: "failed" }))).toBe("That did not go through.");
  });
});
