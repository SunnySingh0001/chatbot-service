/**
 * Secure Markdown renderer supporting GFM tables, disabling images, raw HTML, and non-HTTPS links.
 */
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

export interface SafeMarkdownProps {
  content: string;
}

export const safeComponents: Components = {
  a: ({ href, children }) => {
    // Only permit secure HTTPS hyperlinks; downgrade all others (javascript, data, http) to text
    if (href && /^https:\/\//i.test(href)) {
      return React.createElement(
        "a",
        { href, target: "_blank", rel: "noopener noreferrer nofollow" },
        children
      );
    }
    return React.createElement("span", null, children);
  },
  table: ({ children }) => {
    return React.createElement(
      "div",
      { className: "chat-table-wrapper" },
      React.createElement("table", { className: "chat-table" }, children)
    );
  },
  pre: ({ children }) => {
    return React.createElement(
      "div",
      { className: "chat-code-block-wrapper" },
      React.createElement("pre", { className: "chat-code-pre" }, children)
    );
  },
  code: ({ className, children }) => {
    return React.createElement(
      "code",
      { className: className ? `chat-code-token ${className}` : "chat-inline-code" },
      children
    );
  },
  blockquote: ({ children }) => {
    return React.createElement(
      "blockquote",
      { className: "chat-blockquote" },
      children
    );
  },
  ul: ({ children }) => {
    return React.createElement("ul", { className: "chat-list-ul" }, children);
  },
  ol: ({ children }) => {
    return React.createElement("ol", { className: "chat-list-ol" }, children);
  },
};

export const SafeMarkdown: React.FC<SafeMarkdownProps> = ({ content }) => {
  return React.createElement(
    ReactMarkdown,
    {
      remarkPlugins: [remarkGfm],
      disallowedElements: ["img"],
      components: safeComponents,
    },
    content
  );
};
