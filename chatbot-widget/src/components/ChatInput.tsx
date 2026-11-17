/**
 * Chat input box with character counter, send button, stop streaming button, and keyboard shortcuts.
 */
import React, { ChangeEvent, KeyboardEvent, useState } from "react";
import { MAX_USER_CHARS } from "../services/historyManager";

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop: () => void;
  isStreaming: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, onStop, isStreaming }) => {
  const [text, setText] = useState("");

  const trimmed = text.trim();
  const isOverLimit = text.length > MAX_USER_CHARS;
  const canSend = trimmed.length > 0 && !isOverLimit && !isStreaming;

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) {
        onSend(trimmed);
        setText("");
      }
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSend) {
      onSend(trimmed);
      setText("");
    }
  };

  return (
    <form className="chat-input-form" onSubmit={handleSubmit} aria-label="Chat input form">
      <div className="chat-textarea-wrapper">
        <textarea
          className={`chat-textarea ${isOverLimit ? "textarea-error" : ""}`}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Type your question... (Shift+Enter for new line)"
          rows={2}
          disabled={isStreaming}
          aria-label="Ask a question"
          aria-invalid={isOverLimit}
        />
        <div className="chat-input-footer">
          <span
            className={`chat-char-counter ${isOverLimit ? "counter-limit-exceeded" : ""}`}
            aria-live="polite"
          >
            {text.length} / {MAX_USER_CHARS}
            {isOverLimit && " (Exceeds maximum allowed length)"}
          </span>

          <div className="chat-actions">
            {isStreaming ? (
              <button
                type="button"
                className="chat-btn chat-stop-btn"
                onClick={onStop}
                aria-label="Stop generating response"
                title="Stop generation"
              >
                ⏹ Stop
              </button>
            ) : (
              <button
                type="submit"
                className="chat-btn chat-send-btn"
                disabled={!canSend}
                aria-label="Send question"
                title="Send"
              >
                Send ➤
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
};
