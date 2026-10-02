import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import ErrorBoundary from "./ErrorBoundary";

function Boom(): never {
  throw new Error("kaboom");
}

test("a render error shows a recovery screen, never a blank page", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const onBack = vi.fn();
  render(<ErrorBoundary onBack={onBack}><Boom /></ErrorBoundary>);
  expect(screen.getByText("Something went wrong on this screen.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Back to cases" }));
  expect(onBack).toHaveBeenCalled();
});

test("moving to another route (new key) resets the boundary", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const { rerender } = render(<ErrorBoundary key="a" onBack={() => {}}><Boom /></ErrorBoundary>);
  expect(screen.getByText("Something went wrong on this screen.")).toBeInTheDocument();
  rerender(<ErrorBoundary key="b" onBack={() => {}}><p>other screen</p></ErrorBoundary>);
  expect(screen.getByText("other screen")).toBeInTheDocument();
});

test("children render normally when nothing throws", () => {
  render(<ErrorBoundary onBack={() => {}}><p>fine</p></ErrorBoundary>);
  expect(screen.getByText("fine")).toBeInTheDocument();
});
