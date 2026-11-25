/**
 * Enterprise ChatGPT/Gemini-inspired message composer with auto-resizing textarea, Enter to send, Shift+Enter for newline, circular send/stop button, and character constraints.
 */
import React, { ChangeEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { MAX_USER_CHARS } from "../services/historyManager";
import {
  hasActiveWorkspaceModal,
  shouldAutoFocusComposer,
  shouldRedirectKeyToComposer,
} from "../services/composerFocus";
import { SendIcon, StopIcon } from "./ui/Icons";

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled?: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSend,
  onStop,
  isStreaming,
  disabled = false,
}) => {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const trimmed = text.trim();
  const isOverLimit = text.length > MAX_USER_CHARS;
  const isBlocked = isStreaming || disabled;
  const canSend = trimmed.length > 0 && !isOverLimit && !isBlocked;

  // Auto-focus composer on desktop mount and when streaming re-enables the input
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea || typeof window === "undefined" || typeof document === "undefined") {
      return;
    }

    const activeEl = document.activeElement as HTMLElement | null;
    const isOtherInputFocused =
      activeEl !== null &&
      activeEl !== textarea &&
      (["INPUT", "TEXTAREA", "SELECT"].includes(activeEl.tagName) || activeEl.isContentEditable);

    if (
      !isOtherInputFocused &&
      shouldAutoFocusComposer(window.innerWidth, hasActiveWorkspaceModal(document), isBlocked)
    ) {
      textarea.focus({ preventScroll: true });
    }
  }, [isBlocked]);

  // Redirect printable keystrokes anywhere in the workspace into the chat composer
  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") return;

    const handleGlobalKeyDown = (e: globalThis.KeyboardEvent) => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      const activeEl = document.activeElement as HTMLElement | null;

      if (
        shouldRedirectKeyToComposer({
          key: e.key,
          ctrlKey: e.ctrlKey,
          metaKey: e.metaKey,
          altKey: e.altKey,
          isComposing: e.isComposing,
          isDisabled: isBlocked,
          isComposerFocused: activeEl === textarea,
          activeTagName: activeEl?.tagName,
          isActiveContentEditable: Boolean(activeEl?.isContentEditable),
          hasOpenDialog: hasActiveWorkspaceModal(document),
        })
      ) {
        textarea.focus({ preventScroll: true });
      }
    };

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isBlocked]);

  // Auto-resize textarea height
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, 44), 180);
    textarea.style.height = `${nextHeight}px`;
  }, [text]);

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) {
        onSend(trimmed);
        setText("");
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
        }
      }
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSend) {
      onSend(trimmed);
      setText("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  };

  const handlePillClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement)?.closest("button")) return;
    textareaRef.current?.focus();
  };

  return (
    <form className="chat-input-container" onSubmit={handleSubmit} aria-label="Ask a question">
      <div
        className={`chat-input-pill-box ${isOverLimit ? "input-over-limit" : ""} ${isStreaming ? "input-streaming" : ""}`}
        onClick={handlePillClick}
      >
        <textarea
          ref={textareaRef}
          className="chat-input-textarea"
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Ask Fakhri AI anything..."
          rows={1}
          disabled={isStreaming || disabled}
          aria-label="Message prompt"
        />

        <div className="chat-input-inline-footer">
          <div className="chat-input-meta-hint">
            <span className="chat-input-scope-chip">Read-only · Role Scoped</span>
            <span className="chat-input-newline-hint">Shift + Enter for new line</span>
          </div>

          <div className="chat-input-btn-wrap">
            {isStreaming ? (
              <button
                type="button"
                className="chat-circular-btn chat-stop-btn-circle"
                onClick={onStop}
                aria-label="Stop generation"
                title="Stop generating"
              >
                <StopIcon size={14} />
              </button>
            ) : (
              <button
                type="submit"
                className={`chat-circular-btn chat-send-btn-circle ${canSend ? "can-send" : ""}`}
                disabled={!canSend}
                aria-label="Send message"
                title="Send message (Enter)"
              >
                <SendIcon size={15} />
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="chat-input-bottom-bar">
        <span className="chat-disclaimer-subtext">
          Internal AI can make mistakes. Verify important facts with company documents.
        </span>
        {text.length > 0 && (
          <span className={`chat-char-count ${isOverLimit ? "char-limit-error" : ""}`}>
            {text.length}/{MAX_USER_CHARS}
          </span>
        )}
      </div>
    </form>
  );
};
