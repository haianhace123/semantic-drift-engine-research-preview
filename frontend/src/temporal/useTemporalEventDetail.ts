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
  getTemporalEventById,
} from "./api";

import type {
  TemporalEvent,
} from "./types";

export type TemporalEventDetailState =
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
      event: TemporalEvent;
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

const INITIAL_STATE: TemporalEventDetailState = {
  status: "idle",
};

export function useTemporalEventDetail(
  eventId: string | null,
) {
  const [state, setState] =
    useState<TemporalEventDetailState>(
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

    void getTemporalEventById(
      eventId,
      controller.signal,
    )
      .then((event) => {
        if (controller.signal.aborted) {
          return;
        }

        setState({
          status: "ready",
          eventId,
          event,
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
            "The requested event could not be loaded.",
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
