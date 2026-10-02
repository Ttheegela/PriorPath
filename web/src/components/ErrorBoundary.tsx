import { Component, type ReactNode } from "react";

export default class ErrorBoundary extends Component<{ onBack: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="space-y-3 border border-black bg-white p-4">
        <p className="font-medium">Something went wrong on this screen.</p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              this.props.onBack();
              this.setState({ failed: false });
            }}
            className="rounded border border-black px-3 py-1.5"
          >
            Back to cases
          </button>
          <button type="button" onClick={() => window.location.reload()} className="rounded bg-black px-3 py-1.5 text-white">Reload</button>
        </div>
      </div>
    );
  }
}
