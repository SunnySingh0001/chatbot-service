/**
 * Fakhri ERP AI — Unified Conversation & Knowledge Search Modal
 * Searches across active conversation titles, message contents, and enterprise knowledge topics.
 */
import React, { useState, useEffect, useRef } from "react";
import { SearchIcon, XIcon, MessageSquareIcon } from "../ui/Icons";
import { IconButton } from "../ui/IconButton";
import {
  StoredConversation,
  searchConversations,
  SearchResultItem,
  formatConversationTime,
} from "../../services/sessionStore";

export interface SearchModalProps {
  isOpen: boolean;
  activeRole?: string;
  conversations: StoredConversation[];
  onClose: () => void;
  onSelectQuery: (query: string) => void;
  onSelectConversation?: (sessionId: string) => void;
}

const COMMON_TOPICS = [
  { tag: "Policies", query: "Maternity leave policy aur leave rules kya hain?", allowedRoles: ["*"] },
  { tag: "HR", query: "Disha Damwani ke under reporting hierarchy batao", allowedRoles: ["super-admin", "founder", "hr", "operations"] },
  { tag: "Operations", query: "Pending operational tasks kitne hain?", allowedRoles: ["super-admin", "founder", "operations", "hr"] },
  { tag: "Operations", query: "FBA packaging and prep guidelines kya hain?", allowedRoles: ["*"] },
  { tag: "Clients", query: "Amazon Kickstart plan ke active clients dikhao", allowedRoles: ["super-admin", "founder", "operations", "finance"] },
  { tag: "Invoices", query: "Pending unpaid invoices list dikhao", allowedRoles: ["super-admin", "founder", "finance"] },
  { tag: "Profile", query: "Mera role aur department kya hai?", allowedRoles: ["*"] },
];

export const SearchModal: React.FC<SearchModalProps> = ({
  isOpen,
  activeRole = "none",
  conversations,
  onClose,
  onSelectQuery,
  onSelectConversation,
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setSearchTerm("");
      setSearchResults([]);
    }
  }, [isOpen]);

  // Debounced search across conversations
  useEffect(() => {
    if (!searchTerm.trim()) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }

    setIsSearching(true);
    const timer = setTimeout(() => {
      const results = searchConversations(conversations, searchTerm);
      setSearchResults(results);
      setIsSearching(false);
    }, 120);

    return () => clearTimeout(timer);
  }, [searchTerm, conversations]);

  if (!isOpen) return null;

  const roleTopics = COMMON_TOPICS.filter(
    (t) =>
      t.allowedRoles.includes("*") ||
      activeRole === "super-admin" ||
      activeRole === "founder" ||
      t.allowedRoles.includes(activeRole)
  );

  const filteredTopics = roleTopics.filter(
    (item) =>
      item.query.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.tag.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const hasConversations = searchResults.length > 0;
  const hasTopics = filteredTopics.length > 0;
  const isQueryActive = searchTerm.trim().length > 0;
  const hasNoResults = isQueryActive && !hasConversations && !hasTopics && !isSearching;

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-search-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Search conversations, messages, and topics"
      >
        {/* Search Input Bar */}
        <div className="fkh-search-input-header">
          <SearchIcon size={18} className="fkh-search-icon" />
          <input
            ref={inputRef}
            type="text"
            className="fkh-search-input-field"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search conversations, messages, or topics..."
            aria-label="Search conversations and topics"
          />
          {searchTerm && (
            <IconButton
              icon={<XIcon size={14} />}
              title="Clear search"
              aria-label="Clear search"
              onClick={() => setSearchTerm("")}
              size="sm"
            />
          )}
          <IconButton
            icon={<XIcon size={16} />}
            title="Close Search"
            aria-label="Close Search"
            onClick={onClose}
            size="sm"
          />
        </div>

        {/* Search Results Area */}
        <div className="fkh-search-results-list">
          {isSearching && (
            <div className="fkh-search-loading-row">
              <span className="fkh-search-spinner" />
              <span>Searching conversations and topics...</span>
            </div>
          )}

          {hasNoResults && (
            <div className="fkh-search-empty-state">
              <p className="fkh-search-empty-text">
                No conversations or topics found matching <strong>"{searchTerm}"</strong>.
              </p>
              <span className="fkh-search-empty-hint">Try searching for keywords like "leave", "clients", or "invoices".</span>
            </div>
          )}

          {/* Section 1: Matching Past Conversations */}
          {hasConversations && (
            <div className="fkh-search-results-section">
              <span className="fkh-search-section-label">
                Matching Conversations ({searchResults.length})
              </span>
              {searchResults.map((item) => (
                <button
                  key={item.conv.id}
                  type="button"
                  className="fkh-search-result-row fkh-conversation-result-row"
                  onClick={() => {
                    onSelectConversation?.(item.conv.id);
                    onClose();
                  }}
                >
                  <div className="fkh-search-result-left">
                    <MessageSquareIcon size={16} className="fkh-search-msg-icon" />
                    <div className="fkh-search-conv-details">
                      <span className="fkh-search-conv-title">{item.conv.title}</span>
                      {item.matchSnippet && (
                        <span className="fkh-search-match-snippet">"{item.matchSnippet}"</span>
                      )}
                    </div>
                  </div>
                  <span className="fkh-search-conv-time">
                    {formatConversationTime(item.conv.updatedAt || item.conv.createdAt)}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Section 2: Knowledge Topics */}
          {hasTopics && (
            <div className="fkh-search-results-section">
              <span className="fkh-search-section-label">
                {isQueryActive ? "Suggested Knowledge Topics" : "Quick Enterprise Queries"}
              </span>
              {filteredTopics.map((item, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="fkh-search-result-row"
                  onClick={() => {
                    onSelectQuery(item.query);
                    onClose();
                  }}
                >
                  <span className="fkh-search-result-tag">{item.tag}</span>
                  <span className="fkh-search-result-text">{item.query}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="fkh-search-footer-hint">
          <span>Select any result to navigate or execute immediately.</span>
          <span className="fkh-kbd-shortcut">Esc to close</span>
        </div>
      </div>
    </div>
  );
};
