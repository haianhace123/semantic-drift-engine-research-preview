
import { apiGet } from "./client";

export type UserRole = "admin" | "user" | "viewer";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  organization_id: string;
  email_verified: boolean;
  // Optional only for compatibility with older deployed API instances.
  password_login_enabled?: boolean;
  google_connected: boolean;
  github_connected: boolean;
  last_login: string | null;
}

export function getCurrentUser(
  signal?: AbortSignal,
): Promise<CurrentUser> {
  return apiGet<CurrentUser>("/api/auth/me", signal);
}
