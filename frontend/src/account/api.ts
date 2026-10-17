import {
  apiGet,
  apiMutation,
} from "../api/client";

import type {
  CurrentUser,
} from "../api/auth";

export interface AuthSession {
  id: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  ip_address: string | null;
  user_agent: string | null;
  is_current: boolean;
}

export interface SessionsResponse {
  sessions: AuthSession[];
  count: number;
}

export interface SessionRevocationResponse {
  message: string;
  session_id: string;
  current_session_revoked: boolean;
}

export interface RevokeAllSessionsResponse {
  message: string;
  revoked_count: number;
}

export interface MessageResponse {
  message: string;
}

export interface ChangePasswordResponse {
  message: string;
  reauthenticate: boolean;
}

export interface CreateApiKeyRequest {
  name: string;
  permissions: {
    read: boolean;
    write: boolean;
  };
}

export interface CreatedApiKey {
  id: string;
  name: string;
  key: string;
  permissions: Record<string, unknown>;
}

export interface AccountExport {
  user: CurrentUser;
  consents: Array<{
    type: string;
    granted: boolean;
    policy_version: string;
    created_at: string | null;
  }>;
}

export interface ApiKeyRecord {
  id: string;
  name: string;
  permissions: Record<string, unknown>;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string | null;
}

export interface ApiKeysResponse {
  data: ApiKeyRecord[];
}

export function getSessions(
  signal?: AbortSignal,
): Promise<SessionsResponse> {
  return apiGet<SessionsResponse>(
    "/api/auth/sessions",
    signal,
  );
}

export function revokeSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionRevocationResponse> {
  return apiMutation<SessionRevocationResponse>(
    `/api/auth/sessions/${sessionId}`,
    "DELETE",
    undefined,
    signal,
  );
}

export function revokeAllSessions(
  signal?: AbortSignal,
): Promise<RevokeAllSessionsResponse> {
  return apiMutation<RevokeAllSessionsResponse>(
    "/api/auth/sessions/revoke-all",
    "POST",
    undefined,
    signal,
  );
}

export function signOut(
  signal?: AbortSignal,
): Promise<MessageResponse> {
  return apiMutation<MessageResponse>(
    "/api/auth/logout",
    "POST",
    undefined,
    signal,
  );
}

export function updateProfile(
  fullName: string,
  signal?: AbortSignal,
): Promise<CurrentUser> {
  return apiMutation<
    CurrentUser,
    { full_name: string }
  >(
    "/api/auth/me",
    "PUT",
    { full_name: fullName },
    signal,
  );
}

export function getApiKeys(
  signal?: AbortSignal,
): Promise<ApiKeysResponse> {
  return apiGet<ApiKeysResponse>(
    "/api/auth/api-keys",
    signal,
  );
}


export function changePassword(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string,
  signal?: AbortSignal,
): Promise<ChangePasswordResponse> {
  return apiMutation<
    ChangePasswordResponse,
    {
      current_password: string;
      new_password: string;
      confirm_password: string;
    }
  >(
    "/api/auth/change-password",
    "PUT",
    {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    },
    signal,
  );
}

export function createApiKey(
  request: CreateApiKeyRequest,
  signal?: AbortSignal,
): Promise<CreatedApiKey> {
  return apiMutation<CreatedApiKey, CreateApiKeyRequest>(
    "/api/auth/api-keys",
    "POST",
    request,
    signal,
  );
}

export function revokeApiKey(
  keyId: string,
  signal?: AbortSignal,
): Promise<MessageResponse> {
  return apiMutation<MessageResponse>(
    `/api/auth/api-keys/${keyId}`,
    "DELETE",
    undefined,
    signal,
  );
}

export function exportAccount(
  signal?: AbortSignal,
): Promise<AccountExport> {
  return apiGet<AccountExport>(
    "/api/auth/me/export",
    signal,
  );
}

export function deleteAccount(
  signal?: AbortSignal,
): Promise<MessageResponse> {
  return apiMutation<MessageResponse>(
    "/api/auth/me",
    "DELETE",
    undefined,
    signal,
  );
}
