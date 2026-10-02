import { useCallback, useEffect, useState } from "react";

export type Route = { name: "queue" } | { name: "case"; id: string } | { name: "log" };

export function parseRoute(search: string): Route {
  const params = new URLSearchParams(search);
  const id = params.get("case");
  if (id) return { name: "case", id };
  if (params.get("view") === "log") return { name: "log" };
  return { name: "queue" };
}

export function routeHref(r: Route): string {
  if (r.name === "case") return `/?case=${encodeURIComponent(r.id)}`;
  if (r.name === "log") return "/?view=log";
  return "/";
}

export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState(() => parseRoute(window.location.search));
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((r: Route) => {
    window.history.pushState(null, "", routeHref(r));
    setRoute(r);
  }, []);
  return [route, navigate];
}
