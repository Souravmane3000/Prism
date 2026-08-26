/**
 * components/ui/Button.tsx — Glassmorphic electric lime button.
 *
 * Variants: primary (lime), ghost (transparent), danger (red tint).
 * States: default, hover (glow intensifies), loading (spinner), disabled.
 * All colors via CSS variables — no hex values here.
 */

"use client";

import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  children: ReactNode;
}

const sizeStyles: Record<ButtonSize, string> = {
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2 text-sm gap-2",
  lg: "px-6 py-2.5 text-base gap-2.5",
};

export default function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  children,
  className = "",
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading;

  const baseStyles =
    "inline-flex items-center justify-center font-medium rounded-lg transition-all duration-200 cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed";

  const variantStyles: Record<ButtonVariant, string> = {
    primary: "",
    ghost: "",
    danger: "",
  };

  return (
    <button
      {...props}
      disabled={isDisabled}
      className={`${baseStyles} ${sizeStyles[size]} ${variantStyles[variant]} ${className}`}
      style={{
        ...(variant === "primary" && !isDisabled
          ? {
              backgroundColor: "var(--accent-lime)",
              color: "var(--bg-primary)",
              boxShadow: "0 0 12px var(--accent-glow)",
            }
          : variant === "primary" && isDisabled
            ? {
                backgroundColor: "var(--accent-lime-dim)",
                color: "var(--text-dim)",
              }
            : variant === "ghost"
              ? {
                  backgroundColor: "transparent",
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border-dim)",
                }
              : {
                  backgroundColor: "var(--danger-bg)",
                  color: "var(--danger-text)",
                  border: "1px solid var(--danger-border)",
                }),
        ...props.style,
      }}
      onMouseEnter={(e) => {
        if (!isDisabled) {
          const el = e.currentTarget;
          if (variant === "primary") {
            el.style.boxShadow = "0 0 20px var(--accent-glow-strong)";
            el.style.transform = "translateY(-1px)";
          } else if (variant === "ghost") {
            el.style.borderColor = "var(--border-glow)";
            el.style.color = "var(--text-primary)";
          } else {
            el.style.backgroundColor = "var(--danger-bg-hover)";
          }
        }
        props.onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        if (!isDisabled) {
          const el = e.currentTarget;
          if (variant === "primary") {
            el.style.boxShadow = "0 0 12px var(--accent-glow)";
            el.style.transform = "translateY(0)";
          } else if (variant === "ghost") {
            el.style.borderColor = "var(--border-dim)";
            el.style.color = "var(--text-secondary)";
          } else {
            el.style.backgroundColor = "var(--danger-bg)";
          }
        }
        props.onMouseLeave?.(e);
      }}
    >
      {loading && (
        <Loader2
          size={size === "sm" ? 12 : size === "md" ? 14 : 16}
          className="animate-spin"
        />
      )}
      {children}
    </button>
  );
}
