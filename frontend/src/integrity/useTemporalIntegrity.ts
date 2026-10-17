import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  ApiError,
} from "../api/client";

import {
  getTemporalIntegrity,
} from "./api";

import type {
  TemporalIntegrityResolution,
} from "./types";

export type TemporalIntegrityState =
  | {
      status: "idle";
    }
  | {
      status: "loading";
      eventId: string;
    }
  | {
      status: "ready";
      eventId: string;
      resolution: TemporalIntegrityResolution;
    }
  | {
      status: "not_found";
      eventId: string;
    }
  | {
      status: "error";
      eventId: string;
      error: ApiError;
    };

const INITIAL_STATE: TemporalIntegrityState = {
  status: "idle",
};

export function useTemporalIntegrity(
  eventId: string | null,
) {
  const [state, setState] =
    useState<TemporalIntegrityState>(
      INITIAL_STATE,
    );

  const controllerRef =
    useRef<AbortController | null>(
      null,
    );

  const load = useCallback(() => {
    controllerRef.current?.abort();

    if (eventId === null) {
      controllerRef.current = null;

      setState({
        status: "idle",
      });

      return;
    }

    const controller =
      new AbortController();

    controllerRef.current = controller;

    setState({
      status: "loading",
      eventId,
    });

    void getTemporalIntegrity(
      eventId,
      controller.signal,
    )
      .then((resolution) => {
        if (controller.signal.aborted) {
          return;
        }

        setState({
          status: "ready",
          eventId,
          resolution,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }

        if (
          error instanceof ApiError
          && error.kind === "not_found"
        ) {
          setState({
            status: "not_found",
            eventId,
          });

          return;
        }

        if (error instanceof ApiError) {
          setState({
            status: "error",
            eventId,
            error,
          });

          return;
        }

        setState({
          status: "error",
          eventId,
          error: new ApiError(
            "invalid_response",
            null,
            null,
            "The integrity record could not be loaded.",
          ),
        });
      });
  }, [eventId]);

  useEffect(() => {
    load();

    return () => {
      controllerRef.current?.abort();
    };
  }, [load]);

  return {
    state,
    reload: load,
  };
}