import type { Turn } from "../api/client";

/**
 * Why a turn that was not a chat message (an edit, a snooze) did not do what was asked, or null
 * if it did. The API answers 200 for a turn it stored as failed, so the status is what to read:
 * a success message must wait for it. A refusal that never became a turn is thrown by the client
 * instead and carries its own message.
 */
export function whyNotDone(turn: Turn): string | null {
  if (turn.status === "completed") return null;
  return turn.error?.message || turn.output || "That did not go through.";
}
