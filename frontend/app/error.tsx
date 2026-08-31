"use client";

/**
 * Route error boundary so a Realtime / env-var throw cannot blank the whole tab.
 */

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-4 p-8"
      style={{ color: "var(--text-primary)" }}
    >
      <p className="text-sm font-semibold">This view hit an error</p>
      <p
        className="max-w-md text-center text-xs"
        style={{ color: "var(--text-secondary)" }}
      >
        {error.message || "An unexpected client error occurred."}
      </p>
      <button
        type="button"
        onClick={() => reset()}
        className="rounded-lg px-4 py-2 text-xs font-medium"
        style={{
          backgroundColor: "var(--bg-active)",
          border: "1px solid var(--border-glow)",
          color: "var(--accent-lime)",
        }}
      >
        Try again
      </button>
    </div>
  );
}
