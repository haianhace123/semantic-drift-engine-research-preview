# Release validation: v0.1.0 RC2

Validation date: 17 October 2026
Source Git HEAD: `661e2e8c932591a3f5f8dd1025205bd6abb2fb9f`
Candidate: `v0.1.0-RC2`

## Evidence summary

| Check | Observed result | Scope |
| --- | --- | --- |
| Source integrity | PASS, 869 files | Manifest SHA-256 verification of the staged RC2 source |
| Python and JavaScript regressions | PASS | Selected test suites executed against RC2 |
| Docker Compose configuration | PASS | Configuration validated using a disposable `.env` derived from `.env.example` |
| Docker image build | PASS | Image built from RC2 source on the local Docker Engine |
| Dependency readiness | PASS | Disposable PostgreSQL with pgvector and Redis |
| Fresh migrations | PASS, 50 versions | Applied to a newly created database, not to a live pilot |
| API startup | PASS | Image boot with production-compatible synthetic test settings |
| HTTP acceptance | PASS, 7 cases | Liveness, readiness and selected unauthenticated/authentication/legal entry points |
| Cleanup | PASS | Disposable network, containers and volume removed, no errors reported |

The runtime acceptance report had status `RUNTIME_PASS_PENDING_RELEASE_REVIEW`, confirmed the intended image ID, reported no published host ports, and indicated no modifications to the source repository, RC2 candidate or existing production resources. The earlier API import failure was traced to an invalid test harness configuration. After correcting only the test harness, image-local import and `/health` checks passed, followed by the isolated runtime checks above. RC2 application source and its built image were not changed to resolve the test failure.

## Subsequent source-only UI changes

After RC2 runtime acceptance, a separate 872-file brand-review source tree updated OAuth action labels, CSS layout, JavaScript label restoration and artwork provenance documentation. That tree passed 872/872 SHA-256 checks both before and after testing, 16 targeted Python UI tests, six JavaScript tests and a JavaScript syntax check on 17 October 2026. The isolated Docker runtime acceptance applies to the earlier RC2 image; **the subsequent UI edits were not rebuilt or browser-tested in a new image**. No further application runtime certification is claimed for this source-only release.

## What was not established

These checks do not cover full browser E2E, real transactional email delivery, optional AI providers, production ingress, load and performance, internet-facing security, representative semantic accuracy or completeness of external consumer lineage. The tests are not a general security certification. Publishing the repository is a separate decision from allowing public users to sign up for a hosted deployment.

## Reproduction and safety

The exact results derive from local release acceptance receipts. The isolated runtime used its own resource identifiers and ephemeral test credentials. Do not run recovery or destructive lineage integration tests against an existing installation. The release source includes the test contracts and deployment documentation, but not operational secrets or test database data.
