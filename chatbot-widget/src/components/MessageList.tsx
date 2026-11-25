/**
 * Scrollable list of chat messages with auto-scroll, scroll-lock detection, jump-to-latest control, and role-filtered starter cards.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { UIMessage } from "../types/chat";
import { MessageItem } from "./MessageItem";
import { FakhriLogoIcon, ArrowDownIcon, TemporaryChatIcon } from "./ui/Icons";
import {
  STARTER_PROMPTS,
  getPersonalizedWelcomeCopy,
  StarterPromptItem,
} from "../services/promptStarters";
import { isAtScrollBottom, shouldShowJumpToLatest } from "../services/scrollHelper";

export type { StarterPromptItem };
export { STARTER_PROMPTS };

interface MessageListProps {
  messages: UIMessage[];
  activeRole?: string;
  activePersonaName?: string;
  isTemporary?: boolean;
  onFeedback?: (messageId: string, type: "up" | "down") => void;
  onSelectPrompt?: (prompt: string) => void;
  onRetry?: (messageId: string) => void;
  onRegenerate?: (messageId: string) => void;
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  activeRole = "none",
  activePersonaName,
  isTemporary = false,
  onFeedback,
  onSelectPrompt,
  onRetry,
  onRegenerate,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const autoFollowRef = useRef<boolean>(true);
  const isProgrammaticScrollRef = useRef<boolean>(false);
  const isJumpingRef = useRef<boolean>(false);
  const jumpTimerRef = useRef<number | null>(null);
  const prevMsgCountRef = useRef<number>(messages.length);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const welcomeCopy = getPersonalizedWelcomeCopy(activeRole, activePersonaName);

  const clearJumpTimer = () => {
    if (jumpTimerRef.current !== null) {
      window.clearTimeout(jumpTimerRef.current);
      jumpTimerRef.current = null;
    }
  };

  useEffect(() => () => clearJumpTimer(), []);

  // Snap to latest message immediately when opening an existing thread
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el || messages.length === 0) return;
    isProgrammaticScrollRef.current = true;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => {
      isProgrammaticScrollRef.current = false;
    });
  }, []);

  // Track real user scrolling while ignoring programmatic stream-follow and active smooth jumps
  const handleScroll = () => {
    const el = containerRef.current;
    if (!el || messages.length === 0) {
      setShowJumpToLatest(false);
      return;
    }

    if (isProgrammaticScrollRef.current) {
      return;
    }

    const metrics = {
      scrollHeight: el.scrollHeight,
      scrollTop: el.scrollTop,
      clientHeight: el.clientHeight,
    };

    if (isJumpingRef.current) {
      if (isAtScrollBottom(metrics)) {
        isJumpingRef.current = false;
        clearJumpTimer();
        autoFollowRef.current = true;
        setShowJumpToLatest(false);
      }
      return;
    }

    if (isAtScrollBottom(metrics)) {
      autoFollowRef.current = true;
      setShowJumpToLatest(false);
    } else if (shouldShowJumpToLatest(metrics)) {
      autoFollowRef.current = false;
      setShowJumpToLatest(true);
    }
  };

  // Auto-follow bottom smoothly during streaming and resume following when user sends a prompt
  useEffect(() => {
    const el = containerRef.current;
    if (!el || messages.length === 0) {
      autoFollowRef.current = true;
      setShowJumpToLatest(false);
      prevMsgCountRef.current = messages.length;
      return;
    }

    const latestMsg = messages[messages.length - 1];
    const isNewPromptAdded =
      messages.length > prevMsgCountRef.current &&
      (latestMsg?.role === "user" || messages[messages.length - 2]?.role === "user");
    prevMsgCountRef.current = messages.length;

    if (isNewPromptAdded) {
      isJumpingRef.current = false;
      clearJumpTimer();
      autoFollowRef.current = true;
      setShowJumpToLatest(false);
    }

    if (!autoFollowRef.current) return;

    const rafId = requestAnimationFrame(() => {
      const target = containerRef.current;
      if (!target) return;
      isProgrammaticScrollRef.current = true;
      target.scrollTop = target.scrollHeight;
      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false;
      });
    });
    return () => cancelAnimationFrame(rafId);
  }, [messages]);

  const scrollToBottom = () => {
    const el = containerRef.current;
    if (!el) return;
    clearJumpTimer();
    isJumpingRef.current = true;
    autoFollowRef.current = true;
    setShowJumpToLatest(false);

    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else {
      el.scrollTop = el.scrollHeight;
    }

    jumpTimerRef.current = window.setTimeout(() => {
      isJumpingRef.current = false;
      jumpTimerRef.current = null;
    }, 550);
  };

  // Find index of the latest assistant message for regeneration action
  let lastAssistantIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      lastAssistantIndex = i;
      break;
    }
  }

  return (
    <div className="chat-messages-stage">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="chat-messages-container"
        role="log"
        aria-live="polite"
      >
        {messages.length === 0 ? (
          isTemporary ? (
            <div className="chat-empty-state chat-temporary-empty">
              <div className="fkh-temp-hero-badge" aria-hidden="true">
                <TemporaryChatIcon size={26} />
              </div>
              <h2 className="chat-empty-heading">Temporary Chat</h2>
              <p className="chat-empty-subtext">
                This temporary conversation uses live company knowledge for your active role, and won&apos;t appear in your history or be saved to server sessions.
              </p>
            </div>
          ) : (
            <div className="chat-empty-state">
              <div className="fkh-welcome-lockup">
                <div className="fkh-welcome-hero-badge" aria-hidden="true">
                  <FakhriLogoIcon size={28} />
                </div>
                {activeRole !== "none" && (
                  <span className="fkh-welcome-brand-pill">{welcomeCopy.brandTag}</span>
                )}
              </div>
              <h2 className="chat-empty-heading">{welcomeCopy.heading}</h2>
              <p className="chat-empty-subtext">{welcomeCopy.subtitle}</p>
            </div>
          )
        ) : (
          <div className="chat-conversation-flow">
            {messages.map((msg, index) => (
              <MessageItem
                key={msg.id}
                message={msg}
                isLatestAssistant={index === lastAssistantIndex}
                onFeedback={onFeedback}
                onSelectPrompt={onSelectPrompt}
                onRetry={onRetry}
                onRegenerate={onRegenerate}
              />
            ))}
          </div>
        )}
      </div>

      {/* Non-scrolling overlay dock keeping Jump to Latest anchored cleanly above composer */}
      {messages.length > 0 && showJumpToLatest && (
        <div className="chat-jump-to-latest-dock">
          <button
            type="button"
            className="chat-jump-to-latest-btn"
            onClick={scrollToBottom}
            aria-label="Jump to latest message"
            title="Scroll to latest message"
          >
            <ArrowDownIcon size={14} />
            <span>Jump to latest</span>
          </button>
        </div>
      )}
    </div>
  );
};
