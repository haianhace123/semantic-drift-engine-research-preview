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
  getTemporalReplay,
} from "./api";

import type {
  TemporalReplay,
} from "./types";

export type TemporalReplayState =
  | {
      status: "idle";
    }
  | {
      status: "loading";
      cursorTime: string;
    }
  | {
      status: "ready";
      cursorTime: string;
      replay: TemporalReplay;
    }
  | {
      status: "error";
      cursorTime: string;
      error: ApiError;
    };

const INITIAL_STATE: TemporalReplayState = {
  status: "idle",
};

export function useTemporalReplay(
  cursorTime: string | null,
) {
  const [state, setState] =
    useState<TemporalReplayState>(
      INITIAL_STATE,
    );

  const controllerRef =
    useRef<AbortController | null>(
      null,
    );

  const load = useCallback(() => {
    controllerRef.current?.abort();

    if (cursorTime === null) {
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
      cursorTime,
    });

    void getTemporalReplay(
      cursorTime,
      controller.signal,
    )
      .then((replay) => {
        if (controller.signal.aborted) {
          return;
        }

        setState({
          status: "ready",
          cursorTime,
          replay,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }

        if (error instanceof ApiError) {
          setState({
            status: "error",
            cursorTime,
            error,
          });

          return;
        }

        setState({
          status: "error",
          cursorTime,
          error: new ApiError(
            "invalid_response",
            null,
            null,
            "The temporal replay could not be loaded.",
          ),
        });
      });
  }, [cursorTime]);

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