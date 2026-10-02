import { useEffect, useState } from "react";
import { ensureWorkspace, messageOf } from "./lib/api";
import CaseQueue from "./components/CaseQueue";
import { routeHref, useRoute, type Route } from "./lib/route";

export default function App() {
  const [route, navigate] = useRoute();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureWorkspace().then(
      () => setReady(true),
      (e) => setError(messageOf(e)),
    );
  }, []);

  const link = (r: Route, label: string) => (
    <a
      href={routeHref(r)}
      onClick={(e) => {
        e.preventDefault();
        navigate(r);
      }}
      className={route.name === r.name ? "font-semibold underline" : "hover:underline"}
    >
      {label}
    </a>
  );

  return (
    <div className="min-h-screen bg-neutral-100 text-black">
      <header className="border-b border-black bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-baseline gap-x-6 gap-y-1 px-4 py-3">
          <h1 className="text-xl font-semibold">PriorPath</h1>
          <span className="text-sm text-neutral-600">Medical bill auditor · synthetic demo data · nothing is sent anywhere</span>
          <nav className="ml-auto flex gap-4 text-sm">
            {link({ name: "queue" }, "Cases")}
            {link({ name: "log" }, "Audit log")}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        {error ? (
          <p role="alert" className="border border-black p-3 font-medium">Couldn't start the demo: {error}</p>
        ) : !ready ? (
          <p>Loading…</p>
        ) : route.name === "case" ? (
          <p>Case {route.id}</p>
        ) : route.name === "log" ? (
          <p>Audit log</p>
        ) : (
          <CaseQueue onOpen={(id) => navigate({ name: "case", id })} />
        )}
      </main>
    </div>
  );
}
