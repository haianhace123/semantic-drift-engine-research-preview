/** The URL fragment is the sole authority for workspace navigation. */
export type WorkspaceRoute =
  | { view: "investigation"; eventId: string | null }
  | { view: "sources"; eventId: null }
  | { view: "deliveries"; eventId: null }
  | { view: "account"; eventId: null };

const ACCOUNT_ANCHORS = new Set([
  "#account",
  "#account-identity",
  "#account-sessions",
  "#account-password",
  "#account-api",
  "#account-data",
]);

export function parseWorkspaceRoute(hash: string): WorkspaceRoute {
  if (hash === "#sources") {
    return { view: "sources", eventId: null };
  }

  if (hash === "#deliveries") {
    return { view: "deliveries", eventId: null };
  }

  // AccountSecurity uses document anchors for its subsections. Those are
  // still account routes, not requests to leave the account workspace.
  if (ACCOUNT_ANCHORS.has(hash)) {
    return { view: "account", eventId: null };
  }

  if (hash.startsWith("#event/")) {
    // Unknown IDs must not silently fall back to the newest event.
    return {
      view: "investigation",
      eventId: hash.slice("#event/".length),
    };
  }

  return { view: "investigation", eventId: null };
}

export function eventHash(eventId: string): string {
  return `#event/${encodeURIComponent(eventId)}`;
}
