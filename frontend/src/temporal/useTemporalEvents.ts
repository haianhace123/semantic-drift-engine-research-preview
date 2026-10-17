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
  getTemporalEvents,
} from "./api";

import type {
  TemporalEvent,
} from "./types";

export type TemporalEventsState =
  | {
      status: "loading";
    }
  | {
      status: "ready";
      events: TemporalEvent[];
      total: number;
    }
  | {
      status: "empty";
    }
  | {
      status: "error";
      error: ApiError;
    };

const INITIAL_STATE: TemporalEventsState = {
  status: "loading",
};

export function useTemporalEvents(enabled = true) {
  const [state, setState] =
    useState<TemporalEventsState>(
      INITIAL_STATE,
    );

  const controllerRef =
    useRef<AbortController | null>(
      null,
    );

  const reload = useCallback(() => {
    controllerRef.current?.abort();

    const controller =
      new AbortController();

    controllerRef.current = controller;

    setState({
      status: "loading",
    });

    void getTemporalEvents(
      {
        limit: 500,
        offset: 0,
        order: "desc",
      },
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }

        if (response.data.length === 0) {
          setState({
            status: "empty",
          });

          return;
        }

        setState({
          status: "ready",
          // The API pages newest-first so the initial index shows current
          // monitoring activity; the timeline still needs chronological order.
          events: [...response.data].reverse(),
          total: response.total,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }

        if (error instanceof ApiError) {
          setState({
            status: "error",
            error,
          });

          return;
        }

        setState({
          status: "error",
          error: new ApiError(
            "invalid_response",
            null,
            null,
            "The temporal event stream could not be loaded.",
          ),
        });
      });
  }, []);

  useEffect(() => {
    if (!enabled) {
      // This view does not consume the event ledger. An in-flight response
      // must not leak into a subsequent investigation session.
      controllerRef.current?.abort();
      controllerRef.current = null;
      setState(INITIAL_STATE);
      return;
    }

    reload();

    return () => {
      controllerRef.current?.abort();
    };
  }, [enabled, reload]);

  return {
    state,
    reload,
  };
}