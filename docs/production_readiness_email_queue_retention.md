# Email queue retention and admission (PR.6)

## Failure mode

The email producer previously used approximate Redis Stream `MAXLEN` trimming, and the dead-letter writer did the same. Redis may trim an entry before its consumer group acknowledges it. A pending entry with no remaining payload cannot be delivered by the reclaim path. Increasing `EMAIL_STREAM_MAXLEN` does not make that safe.

## Contract

- `EMAIL_STREAM_MAXLEN` is now a **hard admission limit on physical entries** in the primary stream. The producer checks `XLEN` and executes `XADD` in a single Redis Lua operation. It never evicts existing jobs. A full queue raises `EmailQueueFullError` to the caller; no job is accepted. The calling HTTP route must decide whether to surface that failure or offer a retry mechanism.
- A scheduled retry is promoted from the ZSET only if the main stream has capacity. Its `ZSCORE` check, capacity check, `XADD`, and `ZREM` execute atomically. When full, the retry stays in the ZSET and is tried again on a later worker cycle.
- Successful delivery, scheduled retry, or dead-lettering is terminal for the *current* stream entry. Its `XACK` and `XDEL` execute atomically so acknowledged records do not permanently occupy admission capacity. The scheduled retry remains in its own ZSET until promoted.
- Dead-letter entries are never automatically trimmed. `EMAIL_DEAD_LETTER_MAXLEN` (default 10000) is a separate hard capacity. If the dead-letter stream is full, the transfer fails without acknowledging the source entry; operators must investigate and safely archive/remove records to restore capacity. This favors auditability over silent data loss.

The capacity applies to physical stream entries, **not** an exact count of end-to-end email obligations. Redis AOF is enabled in the supplied Docker Compose configuration, but Redis durability still depends on deployment, persistence policy and backups. The ACK + DELETE step assumes the single email consumer group configured by SDE; sharing the stream with another independent consumer group would require revisiting the deletion policy. This is at-least-once processing, not exactly-once delivery: worker crashes after provider acceptance but before ACK can cause retries, mitigated by the provider idempotency key.

## Operations and rollout

1. Ensure API producers and email workers use the same Redis instance, stream key and `EMAIL_STREAM_MAXLEN`. Capacity must be positive. Set both it and `EMAIL_DEAD_LETTER_MAXLEN` based on expected sustained load and memory; they are admission ceilings, not eviction targets.
2. Track producer failures (`EmailQueueFullError`), `XLEN` on the primary stream, `ZCARD` on the retry ZSET, `XPENDING` for the consumer group, and `XLEN` on the dead-letter stream. Alert on sustained growth and on repeated full-queue errors. Redis memory pressure and eviction settings must also be monitored.
3. Existing stream records ACKed **before** this patch may still occupy capacity because the old worker ACKed without deleting. Before setting a tight limit in an established deployment, inspect `XLEN`, `XPENDING` and consumer group configuration. Do not call `XTRIM` or delete a live stream to create room: pending work can be lost. If historical ACKed records need cleanup, first prove they are neither pending nor unconsumed for any active group and use a controlled maintenance operation.
4. Run the Redis integration tests using test-specific keys. They demonstrate refusal without trimming pending jobs, retry retention at capacity, concurrent admission, and dead-letter fail-closed behavior at capacity. Do not run destructive cleanup against production queues as part of testing.

## Scope limits

This batch does not change caller-level UX for failed verification/reset-email enqueue, does not implement DLQ compaction or automatic redrive, does not make external provider delivery exactly-once, and does not guarantee recovery if Redis itself loses persisted data. Existing authorization routes have historically suppressed some enqueue errors; their user-facing behavior needs a separate explicit decision, rather than being silently changed by queue code.
