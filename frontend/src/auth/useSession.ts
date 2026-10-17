import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  loadSession,
  type SessionState,
} from "./session";

const INITIAL_SESSION: SessionState = {
  status: "loading",
};

export function useSession() {
  const [session, setSession] =
    useState<SessionState>(INITIAL_SESSION);

  const controllerRef =
    useRef<AbortController | null>(null);

  const reload = useCallback(() => {
    controllerRef.current?.abort();

    const controller =
      new AbortController();

    controllerRef.current = controller;

    setSession({
      status: "loading",
    });

    void loadSession(controller.signal)
      .then((nextSession) => {
        if (!controller.signal.aborted) {
          setSession(nextSession);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error(
            "Unexpected session load failure.",
            error,
          );
        }
      });
  }, []);

  useEffect(() => {
    reload();

    return () => {
      controllerRef.current?.abort();
    };
  }, [reload]);

  return {
    session,
    reload,
  };
}