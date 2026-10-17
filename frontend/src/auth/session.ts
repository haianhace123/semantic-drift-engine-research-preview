import { ApiError } from "../api/client";
import {
  getCurrentUser,
  type CurrentUser,
} from "../api/auth";

export type SessionState =
  | {
      status: "loading";
    }
  | {
      status: "authenticated";
      user: CurrentUser;
    }
  | {
      status: "unauthenticated";
    }
  | {
      status: "error";
      error: ApiError;
    };

export async function loadSession(
  signal?: AbortSignal,
): Promise<SessionState> {
  try {
    const user = await getCurrentUser(signal);

    return {
      status: "authenticated",
      user,
    };
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }

    if (
      error instanceof ApiError &&
      error.kind === "unauthenticated"
    ) {
      return {
        status: "unauthenticated",
      };
    }

    if (error instanceof ApiError) {
      return {
        status: "error",
        error,
      };
    }

    return {
      status: "error",
      error: new ApiError(
        "invalid_response",
        null,
        null,
        "The session could not be established.",
      ),
    };
  }
}