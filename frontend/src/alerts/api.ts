import { apiGet, apiMutation } from "../api/client";

export type AlertDeliveryStatus =
  | "pending"
  | "processing"
  | "retryable"
  | "failed"
  | "delivered";

export interface AlertDelivery {
  id: string;
  alert_id: string;
  destination_type: string;
  destination_id: string;
  status: AlertDeliveryStatus;
  attempt_count: number;
  retry_allowed: boolean;
  retry_block_reason: string | null;
  next_attempt_at: string | null;
  claimed_at: string | null;
  last_attempt_at: string | null;
  delivered_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface AlertDeliveryListResponse {
  data: AlertDelivery[];
  total_count: number;
  limit: number;
  offset: number;
}

export interface AlertDeliverySummary {
  organization_id: string;
  observed_at: string;
  total_count: number;
  pending_count: number;
  processing_count: number;
  retryable_count: number;
  failed_count: number;
  delivered_count: number;
  claimable_count: number;
  stale_processing_count: number;
  exhausted_count: number;
  total_attempt_count: number;
  oldest_pending_at: string | null;
  oldest_retryable_at: string | null;
  oldest_processing_claimed_at: string | null;
}

interface AlertDeliveryDetailResponse {
  data: AlertDelivery;
}

interface AlertDeliverySummaryResponse {
  data: AlertDeliverySummary;
}

export function getAlertDeliveries(
  status?: AlertDeliveryStatus,
  limit = 100,
  offset = 0,
  signal?: AbortSignal,
): Promise<AlertDeliveryListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });

  if (status !== undefined) {
    params.set("status", status);
  }

  return apiGet<AlertDeliveryListResponse>(
    `/api/alerts/deliveries?${params.toString()}`,
    signal,
  );
}

export function getAlertDeliverySummary(
  signal?: AbortSignal,
): Promise<AlertDeliverySummaryResponse> {
  return apiGet<AlertDeliverySummaryResponse>(
    "/api/alerts/deliveries/summary",
    signal,
  );
}

export function getAlertDelivery(
  deliveryId: string,
  signal?: AbortSignal,
): Promise<AlertDeliveryDetailResponse> {
  return apiGet<AlertDeliveryDetailResponse>(
    `/api/alerts/deliveries/${encodeURIComponent(deliveryId)}`,
    signal,
  );
}

export function retryAlertDelivery(
  deliveryId: string,
  signal?: AbortSignal,
): Promise<AlertDeliveryDetailResponse> {
  return apiMutation<AlertDeliveryDetailResponse>(
    `/api/alerts/deliveries/${encodeURIComponent(deliveryId)}/retry`,
    "POST",
    undefined,
    signal,
  );
}
