/**
 * Fakhri ERP AI — Standardized Enterprise Button Component
 */
import React from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "outline" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: React.ReactNode;
  iconPosition?: "left" | "right";
  isLoading?: boolean;
}

export const Button: React.FC<ButtonProps> = ({
  variant = "primary",
  size = "md",
  icon,
  iconPosition = "left",
  isLoading = false,
  className = "",
  disabled,
  children,
  ...props
}) => {
  return (
    <button
      type="button"
      className={`fkh-btn fkh-btn-${variant} fkh-btn-${size} ${isLoading ? "is-loading" : ""} ${className}`}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <span className="fkh-spinner" aria-hidden="true" />
      ) : (
        icon && iconPosition === "left" && <span className="fkh-btn-icon-left">{icon}</span>
      )}
      <span className="fkh-btn-label">{children}</span>
      {!isLoading && icon && iconPosition === "right" && (
        <span className="fkh-btn-icon-right">{icon}</span>
      )}
    </button>
  );
};
