# ADR-0046: The MCP server is an adapter over the public API, signed in by access code for now

- **Status:** accepted
- **Date:** 2026-10-02
- **Refines:** PRD 7.18 (FR-18.1 to FR-18.3), which this serves in part and says where it does not

## Context

The product spec has an MCP server as a P1 feature: save, recall, list upcoming and get an item,
authenticated with an API key and metered like the API. API keys (FR-17.2) are not built; the
public API with keys, scopes and metering is planned later. An MCP server is also the one thing
nearly every comparable memory project has, and the product's open glass box is a good fit for it.
Waiting for keys means no MCP server for a long time; building keys first is a sprint of its own.

## Decision

**Build the server now as a thin client of the site's own HTTP API,** in `integrations/mcp/`, its
own package with its own lock file. It signs in the way the site does today (an email and the
access code), keeps the cookie, and exposes five tools (`save`, `recall`, `upcoming`, `get_item`,
`undo`) over stdio.

- **Adapter, not a second implementation.** It calls `/v1/...` and reads the turn's event stream,
  as the web app does (FR-17.1: the web app has no private door, so neither does this). A save is a
  turn: it has a glass box, a memory diff and an undo (FR-18.3, in part). Nothing in the backend
  changes, and the write policy, the spend gate and the quotas apply exactly as they do to chat.
- **Its own package.** The MCP SDK and its dependencies stay out of the API image and its audit
  surface. A contract test in the backend suite runs the client against the real app, so the
  package's stand-in site cannot drift from the real shapes.
- **Words are not changed on the way in.** `save` sends the person's text as it is. A prefix such
  as "remember this:" would be stored with the memory by the offline stand-in and may be by a model;
  instead the reply says plainly when nothing changed, so an agent that sent a question learns it
  was answered, not saved.
- **Errors are the site's own sentences** (a wrong code, a spend pause, an unknown id) and never
  contain the access code.

## What this does not do

It signs in as the person, so it cannot be told apart from them: no per-client scopes, no
per-client metering, no label on the turn, and none of the stricter write rule that FR-18.2 asks
for content from an outside agent. These come with API keys, when the server swaps its sign-in for
a key; the tools and the package stay. Until then it is the owner's tool for the owner's
deployment, and its README says so.

## Alternatives considered

- **Wait for API keys.** Cleaner, and months away; the tool surface does not depend on how it signs
  in.
- **An MCP endpoint inside the API process.** One fewer package, but the MCP SDK joins the image,
  and the server would sit beside the code it is meant to be a client of, which hides the
  "no private door" property this decision wants to keep visible.
- **Reach into the database directly.** Skips the policy, the spend gate and the glass box: the
  things that make a write to memory safe.
