# Alert delivery diagnostic disclosure boundary

## Scope

The AlertDelivery API is tenant-scoped, but `last_error` was previously copied
from a persisted arbitrary exception string into the tenant response. An
unexpected webhook preparation or transport exception could contain secrets,
internal connection details or downstream URLs. Frontend escaping does not
protect confidentiality.

New unexpected worker errors are persisted as stable diagnostic messages. The
API projects both new and historical records through an allowlist: only fixed
application-controlled messages and bare HTTP status messages are exposed.
Unknown messages are replaced with a generic failure. Status, attempts and
retry timestamps remain available to users.

This change does **not** erase historical raw strings from the database, logs or
backups. Internal access to those stores must remain operator-restricted, and a
separate evidence-preserving retention/remediation procedure is required if
historic messages contain credentials. Do not execute bulk deletion or modify
audit history as part of this source patch.

## Acceptance

- Unknown exception strings cannot reach the tenant response.
- Future unexpected webhook exceptions do not persist raw exception text.
- Existing trusted HTTP status diagnostics are maintained.
- Retry/failure transitions and tenant-scoping behavior remain unchanged.
- Real HTTP/integration and deployed runtime acceptance remain separate gates.
