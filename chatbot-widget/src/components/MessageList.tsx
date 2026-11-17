/**
 * Scrollable list of chat messages with auto-scroll and disclaimer banner.
 */
import React, { useEffect, useRef } from "react";
import { UIMessage } from "../types/chat";
import { MessageItem } from "./MessageItem";

interface MessageListProps {
  messages: UIMessage[];
  onFeedback: (messageId: string, type: "up" | "down") => void;
}

export const MessageList: React.FC<MessageListProps> = ({ messages, onFeedback }) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="chat-messages-container" role="log" aria-live="polite">
      <div className="chat-disclaimer-banner" role="note">
        <span className="chat-disclaimer-icon">ℹ️</span>
        <span className="chat-disclaimer-text">
          Aap AI assistant se baat kar rahe hain. Jawab galat ho sakta hai.
        </span>
      </div>

      {messages.length === 0 ? (
        <div className="chat-empty-state">
          <p className="chat-empty-title">Namaste! How can I help you today?</p>
          <p className="chat-empty-sub">
            Ask about company policies, leave guidelines, benefits, or IT setup.
          </p>
        </div>
      ) : (
        messages.map((msg) => (
          <MessageItem key={msg.id} message={msg} onFeedback={onFeedback} />
        ))
      )}

      <div ref={bottomRef} />
    </div>
  );
};
