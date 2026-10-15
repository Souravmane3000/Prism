/**
 * components/layout/MainLayout.tsx — 3-panel workspace.
 *
 * Left sidebar stays 280px. The agent pipeline fills the rest.
 * The review panel (Plan, Files, PR Draft, Debug) docks beside the
 * pipeline when the window can fit it, and becomes an overlay drawer
 * when docking would squeeze the pipeline below a readable width.
 */

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const SIDEBAR_WIDTH = 280;
const AGENT_MIN_WIDTH = 360;
const INSPECTOR_MIN = 320;
const INSPECTOR_MAX = 560;
const INSPECTOR_DEFAULT = 380;
/** Pulling the handle this far past the minimum closes the panel. */
const INSPECTOR_CLOSE_BELOW = INSPECTOR_MIN - 24;

const WIDTH_KEY = "prism_inspector_width";
const OPEN_KEY = "prism_inspector_open";

interface MainLayoutProps {
  sidebar: ReactNode;
  stream: ReactNode;
  inspector: ReactNode;
}

interface InspectorPanelContextValue {
  open: boolean;
  isNarrow: boolean;
  toggle: () => void;
}

const InspectorPanelContext = createContext<InspectorPanelContextValue | null>(
  null,
);

export function useInspectorPanel(): InspectorPanelContextValue {
  const value = useContext(InspectorPanelContext);
  if (!value) {
    throw new Error("useInspectorPanel must be used within MainLayout");
  }
  return value;
}

export function InspectorToggle() {
  const { open, toggle } = useInspectorPanel();
  const label = open
    ? "Hide plan, files, PR draft, and debug"
    : "Show plan, files, PR draft, and debug";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-expanded={open}
      aria-controls="review-panel"
      aria-label={label}
      title={label}
      className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-md transition-colors flex-shrink-0"
      style={{
        color: "var(--accent-lime)",
        border: "1px solid var(--panel-border)",
        backgroundColor: "transparent",
        cursor: "pointer",
      }}
    >
      {open ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
      Review
    </button>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function readStoredWidth(): number {
  try {
    const stored = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) {
      return clamp(stored, INSPECTOR_MIN, INSPECTOR_MAX);
    }
  } catch {
    // localStorage unavailable
  }
  return INSPECTOR_DEFAULT;
}

function readStoredOpen(): boolean {
  try {
    const stored = localStorage.getItem(OPEN_KEY);
    if (stored === "0") return false;
    if (stored === "1") return true;
  } catch {
    // localStorage unavailable
  }
  return true;
}

export default function MainLayout({
  sidebar,
  stream,
  inspector,
}: MainLayoutProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widthRef = useRef(INSPECTOR_DEFAULT);
  const [workspaceWidth, setWorkspaceWidth] = useState<number | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(INSPECTOR_DEFAULT);
  const [preferenceOpen, setPreferenceOpen] = useState(true);
  // Set only when the reviewer opens the panel while it cannot dock.
  // Keeps the overlay up across resizes until they close it.
  const [pinnedOpen, setPinnedOpen] = useState(false);
  const [dragging, setDragging] = useState(false);

  const persistWidth = useCallback((value: number) => {
    try {
      localStorage.setItem(WIDTH_KEY, String(value));
    } catch {
      // ignore
    }
  }, []);

  const persistOpen = useCallback((value: boolean) => {
    try {
      localStorage.setItem(OPEN_KEY, value ? "1" : "0");
    } catch {
      // ignore
    }
  }, []);

  const commitWidth = useCallback((value: number) => {
    widthRef.current = value;
    setInspectorWidth(value);
  }, []);

  useLayoutEffect(() => {
    const storedWidth = readStoredWidth();
    widthRef.current = storedWidth;
    setInspectorWidth(storedWidth);
    setPreferenceOpen(readStoredOpen());

    const el = containerRef.current;
    if (!el) return;

    const measure = () => setWorkspaceWidth(el.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Under 960px the inspector cannot dock without squeezing the pipeline,
  // so it stays closed until the reviewer slides it open as an overlay.
  // Above that, a docked panel is capped so the pipeline keeps 360px.
  const collapseViewport =
    workspaceWidth !== null &&
    workspaceWidth < SIDEBAR_WIDTH + AGENT_MIN_WIDTH + INSPECTOR_MIN;
  const isNarrow = collapseViewport;
  const open = isNarrow ? pinnedOpen : preferenceOpen;

  const centerRoom =
    workspaceWidth === null
      ? INSPECTOR_MAX
      : Math.max(0, workspaceWidth - SIDEBAR_WIDTH);
  const maxDocked =
    workspaceWidth === null
      ? INSPECTOR_MAX
      : workspaceWidth - SIDEBAR_WIDTH - AGENT_MIN_WIDTH;
  const canDock = !isNarrow && maxDocked >= INSPECTOR_MIN;
  const overlay = open && !canDock;
  const panelWidth = isNarrow
    ? Math.min(inspectorWidth, centerRoom)
    : Math.min(
        inspectorWidth,
        INSPECTOR_MAX,
        Math.max(INSPECTOR_MIN, maxDocked),
      );

  const closePanel = useCallback(() => {
    setPinnedOpen(false);
    setPreferenceOpen(false);
    persistOpen(false);
  }, [persistOpen]);

  const toggle = useCallback(() => {
    if (isNarrow) {
      const next = !pinnedOpen;
      setPinnedOpen(next);
      setPreferenceOpen(next);
      persistOpen(next);
      return;
    }
    const next = !preferenceOpen;
    setPreferenceOpen(next);
    if (!next) setPinnedOpen(false);
    persistOpen(next);
  }, [isNarrow, persistOpen, pinnedOpen, preferenceOpen]);

  const applyDragWidth = useCallback(
    (next: number) => {
      if (next < INSPECTOR_CLOSE_BELOW) {
        closePanel();
        return;
      }
      const room = overlay ? centerRoom : Math.max(maxDocked, 0);
      const maxWidth = Math.min(INSPECTOR_MAX, room);
      if (maxWidth < INSPECTOR_MIN) return;
      commitWidth(clamp(next, INSPECTOR_MIN, maxWidth));
    },
    [centerRoom, closePanel, commitWidth, maxDocked, overlay],
  );

  const onDragPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = widthRef.current;
      setDragging(true);
      event.currentTarget.setPointerCapture(event.pointerId);

      const previousCursor = document.body.style.cursor;
      const previousSelect = document.body.style.userSelect;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      const onMove = (moveEvent: PointerEvent) => {
        applyDragWidth(startWidth + (startX - moveEvent.clientX));
      };
      const onUp = () => {
        setDragging(false);
        document.body.style.cursor = previousCursor;
        document.body.style.userSelect = previousSelect;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        persistWidth(widthRef.current);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [applyDragWidth, persistWidth],
  );

  const onHandleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const delta = event.key === "ArrowLeft" ? 16 : -16;
      applyDragWidth(widthRef.current + delta);
      persistWidth(widthRef.current);
    },
    [applyDragWidth, persistWidth],
  );

  const panelContext = useMemo(
    () => ({ open, isNarrow, toggle }),
    [open, isNarrow, toggle],
  );

  const slideLabel = open
    ? "Hide plan, files, PR draft, and debug"
    : "Show plan, files, PR draft, and debug";

  return (
    <InspectorPanelContext.Provider value={panelContext}>
      <div
        ref={containerRef}
        className="flex h-full overflow-hidden relative z-10"
        style={{ minHeight: 0 }}
      >
        <div
          className="h-full overflow-hidden flex-shrink-0"
          style={{ width: SIDEBAR_WIDTH }}
        >
          {sidebar}
        </div>

        <div
          className="relative flex-1 h-full overflow-y-auto min-w-0"
          style={{
            borderLeft: "1px solid var(--panel-border)",
            borderRight: open && !isNarrow ? "1px solid var(--panel-border)" : undefined,
          }}
        >
          {stream}
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            aria-controls="review-panel"
            aria-label={slideLabel}
            title={slideLabel}
            className="absolute top-1/2 z-40 flex items-center justify-center w-5 h-10 rounded-l-md"
            style={{
              right: isNarrow && open ? panelWidth : 0,
              transform: "translateY(-50%)",
              backgroundColor: "var(--bg-panel)",
              border: "1px solid var(--panel-border)",
              borderRight: "none",
              color: "var(--accent-lime)",
              cursor: "pointer",
              transition: dragging ? "none" : "right 220ms ease",
            }}
          >
            {open ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>

        <div
          id="review-panel"
          aria-hidden={!open}
          inert={!open}
          className="h-full overflow-hidden"
          style={{
            width: open || isNarrow ? panelWidth : 0,
            minWidth: open && !isNarrow ? panelWidth : 0,
            flexBasis: isNarrow ? undefined : open ? panelWidth : 0,
            flexGrow: 0,
            position: isNarrow ? "absolute" : "relative",
            right: isNarrow ? 0 : undefined,
            top: isNarrow ? 0 : undefined,
            bottom: isNarrow ? 0 : undefined,
            zIndex: isNarrow ? 30 : undefined,
            transform: isNarrow && !open ? "translateX(100%)" : "translateX(0)",
            transition: dragging ? "none" : "transform 220ms ease",
            pointerEvents: open ? "auto" : "none",
            flexShrink: 0,
            boxShadow:
              isNarrow && open ? "-12px 0 32px rgba(0, 0, 0, 0.35)" : undefined,
            backgroundColor: "var(--bg-panel)",
          }}
        >
          {open && (
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize review panel"
              aria-valuenow={Math.round(panelWidth)}
              aria-valuemin={INSPECTOR_MIN}
              aria-valuemax={INSPECTOR_MAX}
              tabIndex={0}
              onPointerDown={onDragPointerDown}
              onKeyDown={onHandleKeyDown}
              className="absolute left-0 top-0 bottom-0 z-10 w-2 cursor-col-resize"
              style={{ touchAction: "none" }}
            />
          )}
          {inspector}
        </div>
      </div>
    </InspectorPanelContext.Provider>
  );
}
