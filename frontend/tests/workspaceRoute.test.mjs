import assert from "node:assert/strict";
import test from "node:test";

import { eventHash, parseWorkspaceRoute } from "../src/workspace/workspaceRoute.ts";

test("canonical routes and legacy integrity route", () => {
  assert.deepEqual(parseWorkspaceRoute(""), { view: "investigation", eventId: null });
  assert.deepEqual(parseWorkspaceRoute("#integrity"), { view: "investigation", eventId: null });
  assert.deepEqual(parseWorkspaceRoute("#sources"), { view: "sources", eventId: null });
  assert.deepEqual(parseWorkspaceRoute("#deliveries"), { view: "deliveries", eventId: null });
  assert.deepEqual(parseWorkspaceRoute("#account"), { view: "account", eventId: null });
});

test("all account subsection anchors stay in account", () => {
  for (const hash of [
    "#account-identity", "#account-sessions", "#account-password", "#account-api", "#account-data",
  ]) {
    assert.deepEqual(parseWorkspaceRoute(hash), { view: "account", eventId: null });
  }
});

test("event URLs preserve exact identity", () => {
  const id = "dc73d3c6-2d8c-44fa-965e-0e3c91c9f771";
  assert.equal(eventHash(id), `#event/${id}`);
  assert.deepEqual(parseWorkspaceRoute(eventHash(id)), { view: "investigation", eventId: id });
  assert.deepEqual(parseWorkspaceRoute("#event/unavailable"), {
    view: "investigation", eventId: "unavailable",
  });
});

import { useTemporalSelection } from "../src/temporal/useTemporalSelection.ts";

test("an unresolvable event link never substitutes another event", () => {
  const events = [{ id: "one" }, { id: "two" }];
  assert.equal(useTemporalSelection(events, "not-loaded").selectedEventId, null);
  assert.equal(useTemporalSelection(events, null).selectedEventId, "two");
  assert.equal(useTemporalSelection(events, "one").selectedEventId, "one");
});

test("a direct event is accepted only for its exact requested identity", () => {
  const events = [{ id: "loaded" }];
  const direct = { id: "elsewhere" };
  assert.equal(useTemporalSelection(events, "elsewhere", direct).selectedEventId, "elsewhere");
  assert.equal(useTemporalSelection(events, "missing", direct).selectedEventId, null);
  assert.equal(useTemporalSelection(events, "loaded", direct).selectedEventId, "loaded");
});

test("alert delivery route cannot be interpreted as a temporal event", () => {
  assert.notDeepEqual(parseWorkspaceRoute("#deliveries"), parseWorkspaceRoute("#integrity"));
  assert.deepEqual(parseWorkspaceRoute("#event/deliveries"), {
    view: "investigation", eventId: "deliveries",
  });
});
