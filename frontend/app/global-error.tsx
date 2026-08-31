"use client";

import "../styles/tokens.css";

/**
 * Root error boundary. Catches failures that escape app/error.tsx
 * (including layout crashes) so the tab is not replaced by a blank Vercel page.
 */

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "12px",
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <p style={{ fontSize: "14px", fontWeight: 600 }}>Prism hit an error</p>
        <p
          style={{
            fontSize: "12px",
            maxWidth: "420px",
            textAlign: "center",
            color: "var(--text-secondary)",
          }}
        >
          {error.message || "An unexpected client error occurred."}
        </p>
        <button
          type="button"
          onClick={() => reset()}
          style={{
            padding: "8px 16px",
            fontSize: "12px",
            borderRadius: "8px",
            cursor: "pointer",
            background: "var(--bg-active)",
            border: "1px solid var(--border-glow)",
            color: "var(--accent-lime)",
          }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
