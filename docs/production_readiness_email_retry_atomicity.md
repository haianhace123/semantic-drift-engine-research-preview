# PR.5: Email retry promotion atomicity

## Failure mode

The email worker previously removed a due retry from its Redis sorted set before
adding it back to the delivery stream. An interrupted process or failed `XADD`
between these operations could permanently lose a transactional email. The
producer's idempotency key cannot recover a job that is no longer queued.

## Runtime contract

Promotion now executes `ZSCORE`, `XADD`, and `ZREM` inside a single Redis Lua
script. A missing or rescheduled retry is ignored. Concurrent workers cannot
promote the same entry twice. The stream entry is written **before** the retry
member is removed, so an `XADD` failure leaves the retry available for another
cycle. A malformed payload raises an error without consuming the retry.

No provider HTTP call is made by this operation. Delivery remains at-least-once,
with the existing provider idempotency key used across delivery attempts.

## Verification

`tests/integration/test_email_retry_promotion_atomicity.py` exercises the
real Redis server: a wrong-type stream forces an atomic write failure; fixing
the key permits recovery without data loss. A second test runs four concurrent
promoters and verifies one stream entry plus preservation of a future retry.

This change only addresses transfer from the retry set to the delivery stream.
It does not establish exactly-once external email delivery. Redis Stream
retention/trimming and producer-to-queue transaction boundaries require
separate production guarantees before declaring the whole email subsystem
lossless under all capacity and failure conditions.
