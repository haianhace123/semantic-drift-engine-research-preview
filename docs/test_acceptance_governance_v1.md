# Test acceptance governance v1

Tests are product evidence, not an inventory of past implementation choices.

## Mandatory gates

- Unit CI (`pytest -q tests/unit`) checks bounded contracts. A passing unit
  suite alone is not production acceptance.
- Runtime integration CI uses `python -m scripts.ci_runtime_integration_gate`.
  It requires the exact identities of 33 mandatory scenarios across the
  selected modules, and zero skipped/failed/error cases. A duplicate or
  substituted passing case cannot conceal a missing scenario. New, distinct
  passing cases may be added, but the mandatory list changes only after review.
- The existing LangChain PGVector gate remains separate and requires six
  named scenarios with zero skips. The shared report reader retains its
  count-only mode for other gates whose contracts have not been reviewed.
  Neither gate proves the entire integration directory has run.
- Each GitHub Actions runtime job runs in its own `COMPOSE_PROJECT_NAME` and
  cleans up only that isolated project. No application DB/Redis/volume should
  be used for integration acceptance.

## Unsafe fixture removed

`tests/integration/conftest.py` previously ran `SCAN sde:rl:*` and `DELETE`
against the configured Redis before and after many tests. This could wipe
live authentication rate-limit state if tests targeted a shared Redis. The
fixture was removed, along with the opt-out marker and obsolete unit test.

Test setup must not clear shared Redis keys. If future authentication tests
need a clean limiter, provide a *separate disposable Redis instance* through
an isolated Compose project (not `FLUSHDB`, broad `SCAN+DEL`, or direct cleanup
on a runtime Redis), then validate the test's behavior without relying on
resetting live state.

## Remaining risk and next gates

1. Do not treat the 33-case runtime selection as all integration coverage.
2. Update the Windows isolated integration runner and broaden CI only after
   inspecting its current code and exact test environment. The exporter used
   for this batch did not include that PowerShell runner.
3. Classify integration files by required services and environmental flags;
   make skip policy explicit per suite and move risk-critical behavior to
   isolated integration gates.
4. Review source-string UI tests before removing: preserve authorization,
   accessibility, actual user workflows, and customer-visible invariants.
5. Evaluate semantic detection against independently labeled real revisions;
   do not use synthetic performance as the production bar.

## Acceptance scope

This batch addresses a destructive Redis fixture and silent CI skips; it does
not certify all 2,412 static tests nor resolve all unit/integration quality
issues. No product service, schema, or runtime configuration is changed.
