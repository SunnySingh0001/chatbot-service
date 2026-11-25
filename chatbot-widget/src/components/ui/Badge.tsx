/**
 * Fakhri ERP AI — Reusable Status & Role Badge Component
 */
import React from "react";

export type BadgeVariant = "blue" | "green" | "amber" | "red" | "purple" | "cyan" | "slate";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
  size?: "sm" | "md";
  children: React.ReactNode;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = "blue",
  dot = false,
  size = "md",
  className = "",
  children,
  ...props
}) => {
  return (
    <span className={`fkh-badge fkh-badge-${variant} fkh-badge-${size} ${className}`} {...props}>
      {dot && <span className="fkh-badge-dot" />}
      <span className="fkh-badge-text">{children}</span>
    </span>
  );
};
