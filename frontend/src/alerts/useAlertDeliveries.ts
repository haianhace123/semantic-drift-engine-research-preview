import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  getAlertDeliveries,
  getAlertDeliverySummary,
  retryAlertDelivery,
  type AlertDelivery,
  type AlertDeliveryStatus,
  type AlertDeliverySummary,
} from "./api";


const PAGE_SIZE = 20;


interface AlertDeliveriesState {
  deliveries: AlertDelivery[];
  summary: AlertDeliverySummary | null;
  totalCount: number;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
}


function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return "Alert delivery state could not be loaded.";
}


export function useAlertDeliveries() {
  const [status, setStatus] =
    useState<AlertDeliveryStatus | undefined>();

  const [offset, setOffset] = useState(0);

  const [state, setState] =
    useState<AlertDeliveriesState>({
      deliveries: [],
      summary: null,
      totalCount: 0,
      loading: true,
      refreshing: false,
      error: null,
    });

  const [selectedDeliveryId, setSelectedDeliveryId] =
    useState<string | null>(null);

  const [retryingDeliveryId, setRetryingDeliveryId] =
    useState<string | null>(null);

  // A failed retry action is not the same as a failed ledger read.
  const [retryError, setRetryError] = useState<string | null>(null);

  // Superseded requests must never replace a newer filter/page/refresh.
  const latestRequestId = useRef(0);

  const load = useCallback(
    async (
      signal?: AbortSignal,
      refreshing = false,
    ) => {
      const requestId = ++latestRequestId.current;
      setRetryError(null);
      const isCurrent = () =>
        requestId === latestRequestId.current && !signal?.aborted;

      setState((current) => ({
        // A different page/filter has no authority to show prior results.
        deliveries: refreshing ? current.deliveries : [],
        summary: refreshing ? current.summary : null,
        totalCount: refreshing ? current.totalCount : 0,
        loading: !refreshing,
        refreshing,
        error: null,
      }));
      if (!refreshing) setSelectedDeliveryId(null);

      try {
        const [
          deliveriesResponse,
          summaryResponse,
        ] = await Promise.all([
          getAlertDeliveries(
            status,
            PAGE_SIZE,
            offset,
            signal,
          ),
          getAlertDeliverySummary(signal),
        ]);

        if (!isCurrent()) return;

        setState({
          deliveries: deliveriesResponse.data,
          summary: summaryResponse.data,
          totalCount: deliveriesResponse.total_count,
          loading: false,
          refreshing: false,
          error: null,
        });

        setSelectedDeliveryId((current) => {
          if (
            current !== null
            && deliveriesResponse.data.some(
              (delivery) => delivery.id === current,
            )
          ) {
            return current;
          }

          return deliveriesResponse.data[0]?.id ?? null;
        });
      } catch (error) {
        if (!isCurrent()) return;
        if (
          error instanceof DOMException
          && error.name === "AbortError"
        ) {
          return;
        }

        // The former ledger/summary no longer represent a verified read.
        setState({
          deliveries: [],
          summary: null,
          totalCount: 0,
          loading: false,
          refreshing: false,
          error: errorMessage(error),
        });
        setSelectedDeliveryId(null);
      }
    },
    [offset, status],
  );

  useEffect(() => {
    const controller = new AbortController();

    void load(controller.signal);

    return () => {
      controller.abort();
    };
  }, [load]);

  const changeStatus = useCallback(
    (nextStatus?: AlertDeliveryStatus) => {
      setStatus(nextStatus);
      setOffset(0);
    },
    [],
  );

  const refresh = useCallback(async () => {
    await load(undefined, true);
  }, [load]);

  const retry = useCallback(
    async (deliveryId: string) => {
      setRetryingDeliveryId(deliveryId);
      setRetryError(null);

      try {
        await retryAlertDelivery(deliveryId);
        await load(undefined, true);
      } catch (error) {
        // The prior ledger read remains a valid historical observation.
        // Do not misreport a failed retry as a failed data read.
        setRetryError(errorMessage(error));

        throw error;
      } finally {
        setRetryingDeliveryId(null);
      }
    },
    [load],
  );

  const previousPage = useCallback(() => {
    setOffset((current) =>
      Math.max(0, current - PAGE_SIZE),
    );
  }, []);

  const nextPage = useCallback(() => {
    setOffset((current) => {
      const next = current + PAGE_SIZE;

      return next < state.totalCount
        ? next
        : current;
    });
  }, [state.totalCount]);

  const selectedDelivery =
    state.deliveries.find(
      (delivery) =>
        delivery.id === selectedDeliveryId,
    ) ?? null;

  return {
    ...state,
    retryError,
    status,
    offset,
    pageSize: PAGE_SIZE,
    selectedDelivery,
    selectedDeliveryId,
    retryingDeliveryId,
    setSelectedDeliveryId,
    changeStatus,
    refresh,
    retry,
    previousPage,
    nextPage,
  };
}
