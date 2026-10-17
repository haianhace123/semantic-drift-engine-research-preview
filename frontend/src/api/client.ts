export type ApiErrorKind =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "server"
  | "http"
  | "network"
  | "invalid_response"
  | "csrf_unavailable";

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly status: number | null,
    public readonly code: string | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface CsrfResponse {
  ok: boolean;
  csrf_token: string;
}

type MutationMethod = "POST" | "PUT" | "PATCH" | "DELETE";

function errorKind(status: number): ApiErrorKind {
  switch (status) {
    case 401:
      return "unauthenticated";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    default:
      return status >= 500 ? "server" : "http";
  }
}

function serverError(
  value: unknown,
): { code: string | null; message: string | null } {
  if (typeof value !== "object" || value === null) {
    return { code: null, message: null };
  }

  const envelope = value as Record<string, unknown>;
  const detail = envelope.detail ?? envelope;

  if (typeof detail === "string") {
    return { code: null, message: detail };
  }

  if (typeof detail !== "object" || detail === null) {
    return { code: null, message: null };
  }

  const detailObject = detail as Record<string, unknown>;
  const error = detailObject.error ?? detailObject;

  if (typeof error !== "object" || error === null) {
    return { code: null, message: null };
  }

  const fields = error as Record<string, unknown>;

  return {
    code:
      typeof fields.code === "string"
        ? fields.code
        : null,
    message:
      typeof fields.message === "string"
        ? fields.message
        : null,
  };
}

async function requestJson<T>(
  path: `/api/${string}`,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;

  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }

    throw new ApiError(
      "network",
      null,
      null,
      "Could not reach the server.",
    );
  }

  if (!response.ok) {
    const payload: unknown = await response
      .json()
      .catch(() => null);

    const details = serverError(payload);

    throw new ApiError(
      errorKind(response.status),
      response.status,
      details.code,
      details.message ??
        `Request failed with HTTP ${response.status}.`,
    );
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      "invalid_response",
      response.status,
      null,
      "The server returned an invalid JSON response.",
    );
  }
}

export function apiGet<T>(
  path: `/api/${string}`,
  signal?: AbortSignal,
): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    },
    signal,
  );
}

async function getCsrfToken(
  signal?: AbortSignal,
): Promise<string> {
  const response = await apiGet<CsrfResponse>(
    "/api/auth/csrf",
    signal,
  );

  if (
    response.ok !== true ||
    typeof response.csrf_token !== "string" ||
    response.csrf_token.length === 0
  ) {
    throw new ApiError(
      "csrf_unavailable",
      null,
      null,
      "A CSRF token could not be established.",
    );
  }

  return response.csrf_token;
}

export async function apiMutation<TResponse, TBody = unknown>(
  path: `/api/${string}`,
  method: MutationMethod,
  body?: TBody,
  signal?: AbortSignal,
): Promise<TResponse> {
  const csrfToken = await getCsrfToken(signal);

  const headers = new Headers({
    Accept: "application/json",
    "X-CSRF-Token": csrfToken,
  });

  let requestBody: string | undefined;

  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    requestBody = JSON.stringify(body);
  }

  return requestJson<TResponse>(
    path,
    {
      method,
      headers,
      body: requestBody,
    },
    signal,
  );
}