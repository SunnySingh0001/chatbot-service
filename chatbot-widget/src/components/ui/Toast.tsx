/**
 * Fakhri ERP AI — Accessible Toast Notification System
 * Lightweight, zero-dependency feedback banners with smooth micro-interactions and ARIA live announcements.
 */
import React, { createContext, useContext, useState, useCallback } from "react";
import { CheckIcon, AlertTriangleIcon, InfoIcon, XIcon } from "./Icons";

export type ToastType = "info" | "success" | "warning" | "error";

export interface ToastItem {
  id: string;
  message: string;
  type: ToastType;
}

export interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);

export const useToast = (): ToastContextType => {
  const context = useContext(ToastContext);
  if (!context) {
    // Graceful fallback if invoked outside provider
    return {
      showToast: (msg: string) => console.log(`[Toast Fallback]: ${msg}`),
    };
  }
  return context;
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const showToast = useCallback((message: string, type: ToastType = "info") => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts((prev) => [...prev, { id, message, type }]);

    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3600);
  }, []);

  const dismissToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div
        className="fkh-toast-container"
        role="region"
        aria-label="Notification Messages"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`fkh-toast-card fkh-toast-${toast.type}`}
            role="status"
          >
            <div className="fkh-toast-icon-wrap">
              {toast.type === "success" && <CheckIcon size={16} />}
              {toast.type === "warning" && <AlertTriangleIcon size={16} />}
              {toast.type === "error" && <AlertTriangleIcon size={16} />}
              {toast.type === "info" && <InfoIcon size={16} />}
            </div>
            <span className="fkh-toast-text">{toast.message}</span>
            <button
              type="button"
              className="fkh-toast-close-btn"
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss notification"
            >
              <XIcon size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};
