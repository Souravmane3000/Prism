/**
 * components/ui/PrismBackground.tsx — Ambient animated background for Prism.
 *
 * Fixed full-viewport layer (z-0, pointer-events-none) rendered at root layout.
 * Three visual layers:
 *   1. Radial glow aura (centered, soft lime ambient)
 *   2. Top rotating arc beam (border ring, slow 25s spin)
 *   3. Left/right vertical laser glow accents
 *
 * All colors via CSS variables from tokens.css.
 */

export default function PrismBackground() {
  return (
    <div
      aria-hidden="true"
      className="fixed inset-0 z-0 pointer-events-none overflow-hidden"
    >
      {/* Radial glow aura — lime ambient radial gradient */}
      <div
        className="absolute"
        style={{
          top: "-20%",
          left: "50%",
          transform: "translateX(-50%)",
          width: "80vw",
          height: "80vh",
          background:
            "radial-gradient(ellipse at center, var(--accent-glow) 0%, transparent 70%)",
          filter: "blur(40px)",
        }}
      />

      {/* Secondary glow — bottom right subtle accent */}
      <div
        className="absolute"
        style={{
          bottom: "-10%",
          right: "-10%",
          width: "50vw",
          height: "50vh",
          background:
            "radial-gradient(ellipse at center, rgba(163, 230, 53, 0.06) 0%, transparent 70%)",
          filter: "blur(60px)",
        }}
      />

      {/* Top rotating arc beam — asymmetric (top+right border only) so rotation is visible */}
      <div
        className="absolute"
        style={{
          top: "-40vw",
          left: "50%",
          transform: "translateX(-50%)",
          width: "80vw",
          height: "80vw",
        }}
      >
        <div
          className="w-full h-full"
          style={{
            borderRadius: "50%",
            borderTop: "2px solid var(--border-glow)",
            borderRight: "1.5px solid var(--accent-lime-dim)",
            borderBottom: "none",
            borderLeft: "none",
            filter: "drop-shadow(0 0 8px var(--accent-glow)) drop-shadow(0 0 20px var(--accent-lime))",
            animation: "arcSpin 8s linear infinite",
          }}
        />
      </div>

      {/* Inner rotating arc (counter-phased, bottom-left quadrant) */}
      <div
        className="absolute"
        style={{
          top: "-30vw",
          left: "50%",
          transform: "translateX(-50%)",
          width: "60vw",
          height: "60vw",
        }}
      >
        <div
          className="w-full h-full"
          style={{
            borderRadius: "50%",
            borderBottom: "1.5px solid var(--border-subtle)",
            borderLeft: "1px solid var(--accent-lime-dim)",
            borderTop: "none",
            borderRight: "none",
            animation: "arcSpin 14s linear infinite reverse",
          }}
        />
      </div>

      {/* Left vertical laser glow */}
      <div
        className="absolute top-0 left-0 h-full"
        style={{
          width: "1px",
          background:
            "linear-gradient(to bottom, transparent 0%, var(--accent-lime-dim) 30%, var(--accent-lime-muted) 50%, var(--accent-lime-dim) 70%, transparent 100%)",
          opacity: 0.3,
          marginLeft: "280px",
        }}
      />

      {/* Right vertical laser glow */}
      <div
        className="absolute top-0 right-0 h-full"
        style={{
          width: "1px",
          background:
            "linear-gradient(to bottom, transparent 0%, var(--accent-lime-dim) 30%, var(--accent-lime-muted) 50%, var(--accent-lime-dim) 70%, transparent 100%)",
          opacity: 0.3,
          marginRight: "380px",
        }}
      />

      {/* Noise texture overlay for depth */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.03'/%3E%3C/svg%3E\")",
          opacity: 0.4,
        }}
      />
    </div>
  );
}
