import type {
  AlertDelivery,
  AlertDeliveryStatus,
} from "./api";

import {
  useAlertDeliveries,
} from "./useAlertDeliveries";


const STATUS_OPTIONS: Array<{
  value: AlertDeliveryStatus | "";
  label: string;
}> = [
  { value: "", label: "All states" },
  { value: "pending", label: "Pending" },
  { value: "processing", label: "Processing" },
  { value: "retryable", label: "Retryable" },
  { value: "failed", label: "Failed" },
  { value: "delivered", label: "Delivered" },
];


function formatTimestamp(
  value: string | null,
): string {
  if (value === null) {
    return "--";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}


function destinationLabel(
  delivery: AlertDelivery,
): string {
  return [
    delivery.destination_type,
    delivery.destination_id,
  ].filter(Boolean).join(" | ");
}


function retryBlockLabel(
  reason: string | null,
): string {
  switch (reason) {
    case "already_pending":
      return "Delivery is already pending.";

    case "currently_processing":
      return "Delivery is currently processing.";

    case "already_delivered":
      return "Delivery has already been delivered.";

    case "attempt_budget_exhausted":
      return "Delivery attempt budget is exhausted.";

    case "status_not_retryable":
      return "Delivery state does not permit retry.";

    default:
      return "Delivery is not eligible for retry.";
  }
}


function DeliveryInspector({
  delivery,
  retrying,
  canRetry,
  retryBlocked,
  retryOutcomeUnverified,
  onRetry,
}: {
  delivery: AlertDelivery | null;
  retrying: boolean;
  canRetry: boolean;
  retryBlocked: boolean;
  retryOutcomeUnverified: boolean;
  onRetry: (deliveryId: string) => void;
}) {
  if (delivery === null) {
    return (
      <div className="delivery-inspector-empty">
        <span>Delivery inspector</span>
        <p>
          Select a delivery record to inspect its
          operational state.
        </p>
      </div>
    );
  }

  return (
    <section
      className="delivery-inspector"
      aria-labelledby="delivery-inspector-title"
    >
      <header className="delivery-inspector-header">
        <div>
          <span>Selected record</span>
          <h3 id="delivery-inspector-title">
            Delivery inspector
          </h3>
        </div>

        <span
          className={`delivery-status delivery-status-${delivery.status}`}
        >
          {delivery.status}
        </span>
      </header>

      <dl className="delivery-inspector-fields">
        <div>
          <dt>Delivery ID</dt>
          <dd title={delivery.id}>
            {delivery.id}
          </dd>
        </div>

        <div>
          <dt>Destination</dt>
          <dd title={destinationLabel(delivery)}>
            {destinationLabel(delivery) || "--"}
          </dd>
        </div>

        <div>
          <dt>Attempts</dt>
          <dd>{delivery.attempt_count}</dd>
        </div>

        <div>
          <dt>Next attempt</dt>
          <dd>
            {formatTimestamp(
              delivery.next_attempt_at,
            )}
          </dd>
        </div>

        <div>
          <dt>Claimed</dt>
          <dd>
            {formatTimestamp(
              delivery.claimed_at,
            )}
          </dd>
        </div>

        <div>
          <dt>Last attempt</dt>
          <dd>
            {formatTimestamp(
              delivery.last_attempt_at,
            )}
          </dd>
        </div>

        <div>
          <dt>Delivered</dt>
          <dd>
            {formatTimestamp(
              delivery.delivered_at,
            )}
          </dd>
        </div>
      </dl>

      {delivery.last_error !== null && (
        <div className="delivery-error-record">
          <span>Last delivery error</span>
          <p>{delivery.last_error}</p>
        </div>
      )}

      <div className="delivery-inspector-actions">
        <button
          type="button"
          disabled={
            !canRetry
            || !delivery.retry_allowed
            || retrying
            || retryBlocked
          }
          onClick={() => {
            onRetry(delivery.id);
          }}
        >
          {retrying
            ? "Retrying..."
            : "Retry delivery"}
        </button>

        {!canRetry ? (
          <span>
            Retry requires operator write access.
          </span>
        ) : retryOutcomeUnverified ? (
          <span>
            Retry outcome unverified. Refresh the delivery ledger before retrying.
          </span>
        ) : !delivery.retry_allowed ? (
          <span>
            {retryBlockLabel(
              delivery.retry_block_reason,
            )}
          </span>
        ) : null}
      </div>
    </section>
  );
}


export function AlertOperations({
  canRetry,
}: {
  canRetry: boolean;
}) {
  const {
    deliveries,
    summary,
    totalCount,
    loading,
    refreshing,
    error,
    retryError,
    status,
    offset,
    pageSize,
    selectedDelivery,
    selectedDeliveryId,
    retryingDeliveryId,
    setSelectedDeliveryId,
    changeStatus,
    refresh,
    retry,
    previousPage,
    nextPage,
  } = useAlertDeliveries();

  const pageStart =
    totalCount === 0
      ? 0
      : offset + 1;

  const pageEnd = Math.min(
    offset + deliveries.length,
    totalCount,
  );

  const hasPrevious = offset > 0;

  const hasNext =
    offset + pageSize < totalCount;

  return (
    <section
      className="alert-operations"
      aria-labelledby="alert-operations-title"
    >
      <header className="alert-operations-header">
        <div>
          <span className="workspace-kicker">
            Delivery control
          </span>

          <h2 id="alert-operations-title" tabIndex={-1}>
            Alert Operations
          </h2>

          <p>
            Inspect delivery health, isolate failed
            paths, and recover eligible deliveries.
          </p>
        </div>

        <div className="alert-operations-boundary">
          <span>Operational boundary</span>
          <strong>Current delivery plane</strong>
        </div>
      </header>

      <div
        className="delivery-summary"
        aria-label="Alert delivery health summary"
      >
        <div className="delivery-summary-heading">
          <div>
            <span>Delivery health</span>
            <strong>
              {summary === null
                ? error !== null ? "Unavailable" : "Awaiting state"
                : `${summary.total_count} observed`}
            </strong>
          </div>

          <span>
            {summary === null
              ? "--"
              : formatTimestamp(
                  summary.observed_at,
                )}
          </span>
        </div>

        <dl className="delivery-summary-primary">
          <div>
            <dt>Total</dt>
            <dd>{summary?.total_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Pending</dt>
            <dd>{summary?.pending_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Processing</dt>
            <dd>{summary?.processing_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Retryable</dt>
            <dd>{summary?.retryable_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Failed</dt>
            <dd>{summary?.failed_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Delivered</dt>
            <dd>{summary?.delivered_count ?? "--"}</dd>
          </div>
        </dl>

        <dl className="delivery-summary-secondary">
          <div>
            <dt>Claimable</dt>
            <dd>{summary?.claimable_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Stale</dt>
            <dd>
              {summary?.stale_processing_count ?? "--"}
            </dd>
          </div>

          <div>
            <dt>Exhausted</dt>
            <dd>{summary?.exhausted_count ?? "--"}</dd>
          </div>

          <div>
            <dt>Attempts</dt>
            <dd>
              {summary?.total_attempt_count ?? "--"}
            </dd>
          </div>
        </dl>
      </div>

      <div className="delivery-control-plane">
        <section
          className="delivery-ledger"
          aria-labelledby="delivery-ledger-title"
        >
          <header className="delivery-ledger-header">
            <div>
              <span>Delivery ledger</span>
              <h3 id="delivery-ledger-title">
                Recorded deliveries
              </h3>
            </div>

            <strong>
              {error !== null
                ? "Unavailable"
                : loading
                  ? "Loading..."
                  : `${totalCount} result${totalCount === 1 ? "" : "s"}`}
            </strong>
          </header>

          <div className="delivery-ledger-controls">
            <label>
              <span>Status</span>
              <select
                value={status ?? ""}
                disabled={retryingDeliveryId !== null || loading || refreshing}
                onChange={(event) => {
                  const value =
                    event.currentTarget.value;

                  changeStatus(
                    value === ""
                      ? undefined
                      : value as AlertDeliveryStatus,
                  );
                }}
              >
                {STATUS_OPTIONS.map((option) => (
                  <option
                    key={option.value || "all"}
                    value={option.value}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              disabled={loading || refreshing || retryingDeliveryId !== null}
              onClick={() => {
                void refresh();
              }}
            >
              {refreshing
                ? "Refreshing..."
                : "Refresh"}
            </button>
          </div>

          {retryError !== null && (
            <div className="delivery-ledger-error" role="alert">
              <strong>Delivery retry outcome unverified</strong>
              <p>{retryError}</p>
              <p>Previously observed delivery data is retained. Refresh to check the current recorded state before attempting another retry.</p>
            </div>
          )}

          {error !== null && (
            <div
              className="delivery-ledger-error"
              role="alert"
            >
              <strong>
                Delivery state unavailable
              </strong>
              <p>{error}</p>
            </div>
          )}

          {loading ? (
            <div
              className="delivery-ledger-state"
              aria-busy="true"
            >
              Loading delivery state...
            </div>
          ) : error !== null ? (
            <div className="delivery-ledger-state">
              No delivery data is available until a successful read.
            </div>
          ) : deliveries.length === 0 ? (
            <div className="delivery-ledger-state">
              No deliveries match this state.
            </div>
          ) : (
            <div className="delivery-list">
              {deliveries.map((delivery) => {
                const selected =
                  delivery.id === selectedDeliveryId;

                return (
                  <button
                    type="button"
                    key={delivery.id}
                    className={
                      selected
                        ? "delivery-row is-selected"
                        : "delivery-row"
                    }
                    aria-pressed={selected}
                    onClick={() => {
                      setSelectedDeliveryId(
                        delivery.id,
                      );
                    }}
                  >
                    <span
                      className={`delivery-status delivery-status-${delivery.status}`}
                    >
                      {delivery.status}
                    </span>

                    <span className="delivery-row-destination">
                      <strong>
                        {delivery.destination_type}
                      </strong>
                      <span>
                        {delivery.destination_id}
                      </span>
                    </span>

                    <span className="delivery-row-attempts">
                      <small>Attempts</small>
                      <strong>
                        {delivery.attempt_count}
                      </strong>
                    </span>

                    <span className="delivery-row-time">
                      <small>Updated</small>
                      <span>
                        {formatTimestamp(
                          delivery.updated_at
                          ?? delivery.created_at,
                        )}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <footer className="delivery-pagination">
            <span>
              {error !== null ? "Unavailable" : `${pageStart}-${pageEnd} of ${totalCount}`}
            </span>

            <div>
              <button
                type="button"
                disabled={!hasPrevious || loading || refreshing || retryingDeliveryId !== null}
                onClick={previousPage}
              >
                Previous
              </button>

              <button
                type="button"
                disabled={!hasNext || loading || refreshing || retryingDeliveryId !== null}
                onClick={nextPage}
              >
                Next
              </button>
            </div>
          </footer>
        </section>

        <DeliveryInspector
          delivery={selectedDelivery}
          canRetry={canRetry}
          retryBlocked={
            retryError !== null
            || error !== null
            || loading
            || refreshing
            || retryingDeliveryId !== null
          }
          retryOutcomeUnverified={retryError !== null}
          retrying={
            selectedDelivery !== null
            && retryingDeliveryId
              === selectedDelivery.id
          }
          onRetry={(deliveryId) => {
            void retry(deliveryId).catch(
              () => undefined,
            );
          }}
        />
      </div>
    </section>
  );
}