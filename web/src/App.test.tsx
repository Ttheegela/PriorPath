import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

test("waits for the workspace, then shows the queue", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.startsWith("/api/cases") ? [] : { id: "w", created_at: "x" }), { status: 200 })));
  const { default: App } = await import("./App");
  render(<App />);
  expect(screen.getByRole("heading", { name: "PriorPath" })).toBeInTheDocument();
  expect(screen.getByText(/PDF page images are sent to an AI model; nothing else leaves the app/)).toBeInTheDocument();
  expect(await screen.findByText("No cases match.")).toBeInTheDocument();
});

test("shows a readable error when the workspace can't be created", async () => {
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "the demo is full right now" }), { status: 503 })));
  const { default: App } = await import("./App");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("the demo is full right now");
});

test("the title links to the case queue", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    new Response(JSON.stringify(url === "/api/cases" || url.includes("/api/audit-log") ? [] : { id: "w", created_at: "x" }), { status: 200 })));
  window.history.pushState(null, "", "/?view=log");
  const { default: App } = await import("./App");
  render(<App />);
  await userEvent.click(screen.getByRole("link", { name: "PriorPath" }));
  expect(window.location.search).toBe("");
});
