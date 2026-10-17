import { useCallback, useEffect, useState } from "react";

import { parseWorkspaceRoute } from "./workspaceRoute";

/** Browser Back/Forward and in-app links share one route authority. */
export function useWorkspaceRoute() {
  const [route, setRoute] = useState(
    () => parseWorkspaceRoute(window.location.hash),
  );

  useEffect(() => {
    function syncRoute() {
      setRoute(parseWorkspaceRoute(window.location.hash));
    }

    window.addEventListener("hashchange", syncRoute);
    return () => window.removeEventListener("hashchange", syncRoute);
  }, []);

  const navigate = useCallback((hash: string) => {
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    } else {
      setRoute(parseWorkspaceRoute(hash));
    }
  }, []);

  return { route, navigate };
}
