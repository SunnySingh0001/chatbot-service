/**
 * Fakhri ERP AI — Compact Accessible Icon Button Component
 */
import React from "react";

export type IconButtonVariant = "ghost" | "secondary" | "primary" | "outline";
export type IconButtonSize = "sm" | "md" | "lg";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  title: string;
  "aria-label": string;
  icon: React.ReactNode;
}

export const IconButton: React.FC<IconButtonProps> = ({
  variant = "ghost",
  size = "md",
  title,
  "aria-label": ariaLabel,
  icon,
  className = "",
  disabled,
  ...props
}) => {
  return (
    <button
      type="button"
      className={`fkh-icon-btn fkh-icon-btn-${variant} fkh-icon-btn-${size} ${className}`}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
      {...props}
    >
      <span className="fkh-icon-btn-wrap" aria-hidden="true">
        {icon}
      </span>
    </button>
  );
};
