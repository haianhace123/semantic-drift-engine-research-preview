# Product readiness audit — 2026-10-10

## Scope and evidence

Baseline: repository snapshot `semantic_drift_engine (51).zip`, after PR.21. Reviewed the tracked-style source tree, React workspace, static auth pages, FastAPI routes, Docker Compose, workers, semantic-provider configuration, persistence contracts, migrations, and available unit/integration tests. Excluded copied Python environments, `frontend/node_modules`, `.git`, backups, runtime data, and local credentials. No credentials are reproduced in this report.

This is a source-level product and security audit, **not** a penetration test, visual browser acceptance, independent model evaluation, or proof that the deployed environment matches the archive. Any item not exercised against a running environment is described as a risk or verification requirement, not a proven runtime failure.

## Product invariants

1. SDE is a temporal knowledge integrity control plane, not a generic monitoring dashboard or demo chatbot.
2. Separate fetched content, persisted revision, semantic analysis, qualified risk, exact lineage, and authorized downstream invalidation. Never visually collapse unknown or pending evidence into successful analysis.
3. No claimed dependency without exact provenance; no implied system health or live activity without operational evidence.
4. Server-side tenant and permission checks remain the authority. UI role checks are usability measures only.
5. The primary frontend is the compiled React workspace. Avoid two product UIs competing for the same routes.
6. Provider availability, privacy, costs, and downstream safety must be explicit in a customer pilot.

## Findings, ordered by risk

| Priority | Finding | Source evidence | Decision |
|---|---|---|---|
| P0 pilot | The crawler scheduler does not receive `DEEPSEEK_API_KEY`; the API has `env_file: .env`, the crawler does not. A configured host `.env` alone is insufficient for semantic detection in that container. | `docker-compose.yml` `api` / `crawler_scheduler`; `src/diff_engine/deepseek_diff.py` | PR.22: pass only DeepSeek provider variables to the crawler. Never copy the full `.env` to this service. Verify effective configuration without revealing credentials. |
| P0 consistency | A second, obsolete HTML/JavaScript dashboard is still served by the `/dashboard` static mount. Direct `/dashboard/` entry is not the same as the protected React entrypoint `/dashboard/index.html`. | `src/main.py`; old `src/static/index.html`, `css/temporal.css`, `js/temporal-workspace.js`, `js/auth-client.js`, `pages/settings.html` | PR.22: redirect directory URLs to canonical authenticated entry and delete unused legacy dashboard assets. This is a product-authority problem, not demonstrated access to protected API data. |
| P1 trust | Login/signup visually claim `LIVE TEMPORAL SIGNAL`, `MONITORING`, or `READY` using decorative markup without system state. | `src/static/login.html`, `signup.html` | PR.22: replace synthetic operational indicators with factual capability descriptions. |
| P0 commercialization | Terms and privacy pages are minimal, generic text without a named controller/operator, usable contact channel, service-specific processing/disclosure detail, or retention commitments. | `src/static/terms.html`, `privacy.html` | Block external commercial rollout until an owner and legal/privacy review supply accurate terms. Do **not** invent legal claims in code. |
| P1 security hardening | CSP currently permits `script-src 'unsafe-inline'` and third-party CDN origins. Static auth pages use inline scripts/styles, so removing this permission requires migration and tests. | `src/main.py` `SecurityHeadersMiddleware`; `src/static/*.html` | Plan separate CSP/asset consolidation with a report-only compatibility stage; do not break OAuth/password recovery flows. |
| P1 UX architecture | React investigation, source monitoring, and account settings rely on hash states with separate rendering branches; the source view replaces the investigation shell, and account state is managed separately. | `frontend/src/workspace/TemporalWorkspace.tsx` | Next UX batch: one coherent route/navigation authority, persistent workspace wayfinding, back navigation, deep-link tests, keyboard use. Preserve Chronoscope as specialized core view. |
| P1 UX consistency | Login/signup use `auth-context`, forgot/reset use `auth-hero`, and OAuth linking has its own inline purple-accent layout; auth CSS contains overlapping generations of selectors. | `src/static/{login,signup,forgot-password,reset-password,oauth-link}.html`; `src/static/css/auth.css` | Consolidate auth UI around one design system without changing existing form IDs/contracts or auth endpoint flows. |
| P1 operational truth | The vector worker disables embeddings by default; the semantic recovery worker is profile-gated and gap reconciliation is opt-in. A loaded UI alone is not evidence every pipeline stage is running. | `docker-compose.yml`; `scripts/semantic_analysis_recovery_worker.py` | Implement/read existing admin capability preflight in pilot procedure; verify worker modes, configured providers, and data-stage outcomes. Do not silently enable billing or change defaults. |
| P1 evidence UX | Source diagnostics currently describe only the latest two observations, not the full lineage or complete event history. Source listing does not itself provide a complete source-to-event investigation path. | `frontend/src/workspace/SourceMonitoring.tsx`; `src/services/source_diagnostics_service.py` | Provide source-specific event navigation only where event IDs and evidence are authoritative; distinguish pending, failed, unchanged, and qualified evidence. |
| P1 quality evidence | Independent semantic holdout exists as an evaluator, but this archive does not establish measured results on an independently labeled customer-domain corpus. | `scripts/evaluate_semantic_holdout.py`, `docs/semantic_holdout_evaluation.md` | Require actual independent labels, precision/recall, cost, and response time before claiming semantic accuracy. |
| P1 historical evidence provenance | Read-only source inventory identified 60 sources matching the old seeder URL pattern in one organization. The retired script also generated synthetic snapshots, drift events, patterns, anomalies and alerts; it was not an active Compose service. | `docs/seeded_source_provenance_20261010.md`; `scripts/audit_seed_provenance_readonly.py` | Fingerprint that organization and historical evidence before any tenant-scoped reversible isolation. Do not delete records or infer ownership from URLs alone. |
| P2 security validation | Current protections include CSRF, tenant-bound endpoints, schema preflight, secret checks, and fail-closed worker contracts, but code inspection does not prove system-wide penetration safety. | `src/api/auth_routes.py`; `src/api/routes/sources.py`; `scripts/schema_preflight.py`; `src/core/config.py` | Run targeted adversarial tests for auth/session fixation, OAuth linking, tenant boundaries, webhook SSRF, queue backpressure, and migration deployment before external pilot. |

## UI and UX rubric for the next batches

Do not imitate another product's surface decorations. Learn from operational products' hierarchy and contracts:

- **Linear:** prioritized content over chrome, consistent navigation and a calm visual hierarchy. [Linear 2026 design notes](https://linear.app/now/behind-the-latest-design-refresh).
- **Sentry:** event-centered incident pages, evidence/actions separated, contextual expansion instead of permanently opening everything. [Sentry issue detail](https://sentry.io/changelog/new-issue-details-ui-now-available/).
- **Carbon:** empty states must distinguish first use, no results, errors, and missing permission; status must use readable labels in addition to color. [Empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states), [Status indicators](https://www.carbondesignsystem.com/building-blocks/core/patterns/status-indicators).

SDE design direction: compact typography and aligned data, document/asset/revision IDs, one high-signal temporal cursor, explicit evidence provenance, sparse action hierarchy, consistent icon stroke and accessible labels, low-key transitions (120–180 ms), and reduced-motion support. Avoid fake live meters, gradient/glass/glow ornament, generic KPI card grids, emoji icons, meaningless badges, and animated states without data.

## Route-by-route UX coverage for real-browser acceptance

| Surface | Source | Required acceptance |
|---|---|---|
| Sign in | `src/static/login.html`, `js/auth-pages.js` | Email and OAuth success/failure, CSRF, expired session, keyboard, narrow viewport, no false operational status |
| Sign up / verification | `signup.html`, `auth-pages.js` | Real verification flow, rate limits, consent, password rules, duplicate identity privacy |
| Forgot/reset password | `forgot-password.html`, `reset-password.html` | Enumeration resistance, expired/used token, form errors and confirmation |
| OAuth account linking | `oauth-link.html`, `auth-pages.js` | State binding, identity collision, cross-account protection, cancellation |
| Account & sessions | `frontend/src/account/*` | Session revoke, profile edits, delete flows, focus restoration and role checks |
| Source monitoring | `frontend/src/workspace/SourceMonitoring.tsx` | Register → pending → snapshot → outcome → diagnostics; pause/resume, pagination, tenant isolation |
| Change investigation | `ChangeIndex`, `SemanticEvidence`, `IntegrityRecord` | Relevant change list, qualified evidence, pending/failed distinctions, downstream unknown state |
| Temporal replay | `Chronoscope`, `HistoricalEvidence` | Cursor, revision time, snapshot validity, keyboard and mobile viewport |
| Alerts and operators | `AlertOperations`, operations API | Permission, retries, side-effect confirmation, error states |
| Privacy/terms | `src/static/privacy.html`, `terms.html` | Legal text signed off and contacts/retention are factual |

## Batch boundaries

- **PR.22 (immediate):** fix provider env propagation; retire competing legacy dashboard; remove fake auth status; regression proof.
- **PR.23 (UI authority):** unify auth pages and OAuth linking; shared design tokens; validate real forms and error states across desktop/mobile. Do not rewrite auth backend without a demonstrated contract failure.
- **PR.24 (Temporal workspace UX):** navigation and deep links, source-to-event path, evidence visual hierarchy, accessibility and interaction tests.
- **PR.25 (pilot runtime admission):** end-to-end real provider consent, provider/worker readiness, cost and latency, independent semantic eval. Block external pilot on missing security/legal requirements.

Close each batch on evidence; replace or delete obsolete features rather than preserving them out of habit. Do not call the product production-certified based on source review or unit test counts.
