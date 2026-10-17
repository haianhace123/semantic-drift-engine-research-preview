import { useEffect } from "react";

import { useSession } from "../auth/useSession";

import {
  TemporalWorkspace,
} from "../workspace/TemporalWorkspace";


function SessionLoading() {
  return (
    <main aria-busy="true">
      <p>Establishing secure session...</p>
    </main>
  );
}


function SessionError({
  onRetry,
}: {
  onRetry: () => void;
}) {
  return (
    <main>
      <h1>Session unavailable</h1>
      <p>
        The workspace could not verify your session.
        Your authentication state has not been changed.
      </p>
      <button
        type="button"
        onClick={onRetry}
      >
        Retry
      </button>
    </main>
  );
}


export function App() {
  const {
    session,
    reload,
  } = useSession();

  useEffect(() => {
    if (session.status === "unauthenticated") {
      window.location.replace(
        "/login.html",
      );
    }
  }, [session.status]);

  if (
    session.status === "loading"
    || session.status === "unauthenticated"
  ) {
    return <SessionLoading />;
  }

  if (session.status === "error") {
    return (
      <SessionError
        onRetry={reload}
      />
    );
  }

  return (
    <TemporalWorkspace
      user={session.user}
    />
  );
}