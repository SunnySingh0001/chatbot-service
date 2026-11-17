/**
 * Secure Markdown renderer disabling images, raw HTML, and non-HTTPS link protocols.
 */
import React from "react";
import ReactMarkdown from "react-markdown";
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
};

export const SafeMarkdown: React.FC<SafeMarkdownProps> = ({ content }) => {
  return React.createElement(
    ReactMarkdown,
    {
      disallowedElements: ["img"],
      components: safeComponents,
    },
    content
  );
};
