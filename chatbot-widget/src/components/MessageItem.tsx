/**
 * Renders an individual chat message with enterprise typography, copy button, pagination card, feedback, retry, and regeneration.
 */
import React, { useState } from "react";
import { SafeMarkdown } from "./SafeMarkdown";
import { UIMessage } from "../types/chat";
import {
  FakhriLogoIcon,
  CopyIcon,
  CheckIcon,
  ThumbsUpIcon,
  ThumbsDownIcon,
  RefreshCwIcon,
} from "./ui/Icons";

interface MessageItemProps {
  message: UIMessage;
  isLatestAssistant?: boolean;
  onFeedback?: (messageId: string, type: "up" | "down") => void;
  onSelectPrompt?: (prompt: string) => void;
  onRetry?: (messageId: string) => void;
  onRegenerate?: (messageId: string) => void;
}

const parsePagination = (content: string) => {
  if (!content) return null;
  // Match patterns like:
  // "Showing records 1–20 of 1,443" or "Dikhaye gaye employees: 1–20 (Kul: 40)" or "Dikhaye gaye clients: 1–20 (Kul: 1,443)"
  const regex = /(?:Showing|Dikhaye gaye|Records|clients|employees|invoices)[\s\w:]*?(\d+)[\s–-]+(\d+)[\s\w]*?(?:of|kul|out of|total)\s*\*?([\d,]+)\*?/i;
  const match = content.match(regex);
  if (!match) return null;
  const start = parseInt(match[1].replace(/,/g, ""), 10);
  const end = parseInt(match[2].replace(/,/g, ""), 10);
  const total = parseInt(match[3].replace(/,/g, ""), 10);
  if (isNaN(start) || isNaN(end) || isNaN(total) || total <= 0) return null;
  const percent = Math.min(100, Math.max(1, Math.round((end / total) * 100)));
  return { start, end, total, percent };
};

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isLatestAssistant = false,
  onFeedback,
  onSelectPrompt,
  onRetry,
  onRegenerate,
}) => {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const pagination = !isUser && message.content ? parsePagination(message.content) : null;

  const handleCopy = async () => {
    if (!message.content) return;
    let success = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(message.content);
        success = true;
      }
    } catch {
      success = false;
    }

    if (!success) {
      try {
        const textarea = document.createElement("textarea");
        textarea.value = message.content;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        success = document.execCommand("copy");
        document.body.removeChild(textarea);
      } catch {
        success = false;
      }
    }

    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      className={`chat-turn-row chat-turn-${message.role}`}
      role="article"
      aria-label={`${message.role} message`}
    >
      <div className="chat-turn-content">
        {!isUser && (
          <div className="chat-assistant-avatar-badge" aria-hidden="true">
            <FakhriLogoIcon size={17} />
          </div>
        )}

        <div className="chat-turn-body">
          <div className="chat-turn-header">
            <span className="chat-turn-author">{isUser ? "You" : "Fakhri AI"}</span>
            {message.timestamp && (
              <span className="chat-turn-time">{message.timestamp}</span>
            )}
          </div>

          <div className={`chat-bubble-surface ${isUser ? "user-bubble" : "assistant-bubble"}`}>
            {isUser ? (
              <p className="chat-user-text">{message.content}</p>
            ) : (
              <div className="chat-markdown-render">
                {message.isStreaming && !message.content ? (
                  <div className="chat-thinking-indicator" aria-label="Thinking">
                    <span className="thinking-dot"></span>
                    <span className="thinking-dot"></span>
                    <span className="thinking-dot"></span>
                    <span className="thinking-text">Thinking...</span>
                  </div>
                ) : (
                  <>
                    <SafeMarkdown content={message.content || ""} />
                    {message.isStreaming && <span className="chat-streaming-cursor" aria-hidden="true" />}
                  </>
                )}
              </div>
            )}

            {pagination && !message.isStreaming && (() => {
              const content = message.content || "";
              const isEmployee = /employee|karmachari|staff|team member|designation/i.test(content) || pagination.total <= 50;
              const isInvoice = /invoice|billing|payment|paid|unpaid/i.test(content);
              const remaining = pagination.total - pagination.end;
              const nextBatchCount = Math.min(20, Math.max(0, remaining));

              return (
                <div className="chat-pagination-card" aria-label="Pagination Progress">
                  <div className="chat-pagination-top">
                    <div className="chat-pagination-label">
                      <span className="pagination-dot">●</span>
                      <span>
                        Dikhaye gaye: <strong>{pagination.start}–{pagination.end}</strong> (Kul: <strong>{pagination.total.toLocaleString()}</strong>)
                      </span>
                    </div>
                    <span className="pagination-percent-badge">{pagination.percent}% Loaded</span>
                  </div>

                  <div className="chat-progress-track">
                    <div className="chat-progress-fill" style={{ width: `${pagination.percent}%` }} />
                  </div>

                  <div className="chat-quick-prompt-chips">
                    {remaining > 0 && (
                      <button
                        type="button"
                        className="chat-prompt-pill-btn primary-pill"
                        onClick={() => onSelectPrompt?.(
                          isEmployee
                            ? `Agle ${nextBatchCount} employees dikhao`
                            : isInvoice
                              ? `Agle ${nextBatchCount} invoices dikhao`
                              : `Agle ${nextBatchCount} clients dikhao`
                        )}
                      >
                        ⏭️ Agle {nextBatchCount} {isEmployee ? "Employees" : isInvoice ? "Invoices" : "Records"}
                      </button>
                    )}

                    {isEmployee ? (
                      <>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Sales department ke employees dikhao")}
                        >
                          🏢 Sales (13)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Ecom Management department ke employees dikhao")}
                        >
                          📦 Ecom Mgmt (16)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("IT & Development team ke employees dikhao")}
                        >
                          💻 IT & Dev (3)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("DMAX creative team ke employees dikhao")}
                        >
                          🎨 DMAX (4)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Super Admins aur core leadership dikhao")}
                        >
                          👑 Leadership (3)
                        </button>
                      </>
                    ) : isInvoice ? (
                      <>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Paid invoices dikhao")}
                        >
                          ✅ Paid Invoices
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Pending unpaid invoices dikhao")}
                        >
                          ⏳ Pending Invoices
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Elite plan ke clients dikhao")}
                        >
                          👑 Elite Plan (152)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Amazon Kickstart plan ke clients dikhao")}
                        >
                          🚀 Kickstart Plan (71)
                        </button>
                        <button
                          type="button"
                          className="chat-prompt-pill-btn"
                          onClick={() => onSelectPrompt?.("Free plan ke clients dikhao")}
                        >
                          🆓 Free Tier (1,088)
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })()}

            {message.error && (
              <div className="chat-error-callout" role="alert">
                <div className="chat-error-top">
                  <span className="chat-error-icon">⚠️</span>
                  <span className="chat-error-message">{message.error}</span>
                </div>
                {onRetry && (
                  <button
                    type="button"
                    className="chat-retry-btn"
                    onClick={() => onRetry(message.id)}
                  >
                    <RefreshCwIcon size={14} />
                    <span>Retry Query</span>
                  </button>
                )}
              </div>
            )}

            {!isUser && !message.isStreaming && message.sources && message.sources.length > 0 && (
              <div className="chat-sources-container" aria-label="Verified sources">
                <span className="chat-sources-title">Verified Sources:</span>
                <div className="chat-sources-pills">
                  {message.sources.map((src, idx) => (
                    <span key={`${src}-${idx}`} className="chat-source-chip">
                      📄 {src.replace(/^mongo:/, "")}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {!isUser && !message.isStreaming && !message.error && message.content && (
              <div className="chat-action-toolbar" aria-label="Message options">
                <button
                  type="button"
                  className={`chat-action-pill-btn ${copied ? "copied" : ""}`}
                  onClick={handleCopy}
                  aria-label="Copy response"
                  title={copied ? "Copied to clipboard!" : "Copy response"}
                >
                  {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                  <span>{copied ? "Copied" : "Copy"}</span>
                </button>

                {isLatestAssistant && onRegenerate && (
                  <button
                    type="button"
                    className="chat-action-pill-btn"
                    onClick={() => onRegenerate(message.id)}
                    aria-label="Regenerate response"
                    title="Regenerate response"
                  >
                    <RefreshCwIcon size={14} />
                    <span>Regenerate</span>
                  </button>
                )}

                {onFeedback && (
                  <div className="chat-feedback-group" aria-label="Was this helpful?">
                    <button
                      type="button"
                      className={`chat-feedback-pill ${message.feedback === "up" ? "active-up" : ""}`}
                      onClick={() => onFeedback(message.id, "up")}
                      aria-label="Good response"
                      title="Helpful"
                    >
                      <ThumbsUpIcon size={14} />
                    </button>
                    <button
                      type="button"
                      className={`chat-feedback-pill ${message.feedback === "down" ? "active-down" : ""}`}
                      onClick={() => onFeedback(message.id, "down")}
                      aria-label="Bad response"
                      title="Not helpful"
                    >
                      <ThumbsDownIcon size={14} />
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
