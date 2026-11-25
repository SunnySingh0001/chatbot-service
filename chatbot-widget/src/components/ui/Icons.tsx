/**
 * Fakhri ERP AI — Unified Inline SVG Icon System
 * Standardized 24x24 viewBox, stroke-based, zero external dependencies.
 */
import React from "react";

export interface IconProps extends React.SVGProps<SVGSVGElement> {
  size?: number;
  className?: string;
  title?: string;
}

/** Custom Fakhri ERP AI brand mark combining geometric F monogram and 4-point AI spark. */
export const FakhriLogoIcon: React.FC<IconProps> = ({ size = 22, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path
      d="M4.5 6.05A1.55 1.55 0 0 1 6.05 4.5H17.65A1.55 1.55 0 0 1 19.2 6.05A1.55 1.55 0 0 1 17.65 7.6H7.7V10.4H11.15A1.45 1.45 0 0 1 12.6 11.85A1.45 1.45 0 0 1 11.15 13.3H7.7V18A1.6 1.6 0 0 1 6.1 19.6A1.6 1.6 0 0 1 4.5 18V6.05Z"
      fill="currentColor"
    />
    <path
      d="M16.3 11.1Q16.9 14.7 20.5 15.3Q16.9 15.9 16.3 19.5Q15.7 15.9 12.1 15.3Q15.7 14.7 16.3 11.1Z"
      fill="currentColor"
      fillOpacity="0.92"
    />
  </svg>
);

export const SparklesIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
    <path d="M20 3v4" />
    <path d="M22 5h-4" />
    <path d="M4 17v2" />
    <path d="M5 18H3" />
  </svg>
);

export const PlusIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </svg>
);

export const PanelLeftIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <rect width="18" height="18" x="3" y="3" rx="2" />
    <path d="M9 3v18" />
  </svg>
);

export const MessageSquareIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

export const ShieldCheckIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

export const DatabaseIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <ellipse cx="12" cy="5" rx="9" ry="3" />
    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
  </svg>
);

export const CopyIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
    <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
  </svg>
);

export const CheckIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

export const StopIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <rect width="14" height="14" x="5" y="5" rx="2" />
  </svg>
);

export const SendIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M12 19V5" />
    <path d="m5 12 7-7 7 7" />
  </svg>
);

export const ThumbsUpIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M7 10v12" />
    <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h3" />
  </svg>
);

export const ThumbsDownIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M17 14V2" />
    <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3" />
  </svg>
);

export const TerminalIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" x2="20" y1="19" y2="19" />
  </svg>
);

export const SettingsIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export const UserIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </svg>
);

export const XIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </svg>
);

export const RefreshCwIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
    <path d="M8 16H3v5" />
  </svg>
);

export const ChevronRightIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="m9 18 6-6-6-6" />
  </svg>
);

export const LayersIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <polygon points="12 2 2 7 12 12 22 7 12 2" />
    <polyline points="2 17 12 22 22 17" />
    <polyline points="2 12 12 17 22 12" />
  </svg>
);

export const PanelRightIcon: React.FC<IconProps> = ({ size = 20, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <rect width="18" height="18" x="3" y="3" rx="2" />
    <path d="M15 3v18" />
  </svg>
);

export const SearchIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </svg>
);

export const HelpCircleIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <circle cx="12" cy="12" r="10" />
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
    <path d="M12 17h.01" />
  </svg>
);

export const ArrowDownIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M12 5v14" />
    <path d="m19 12-7 7-7-7" />
  </svg>
);

export const PencilIcon: React.FC<IconProps> = ({ size = 14, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
    <path d="m15 5 4 4" />
  </svg>
);

export const TrashIcon: React.FC<IconProps> = ({ size = 14, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M3 6h18" />
    <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
    <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
    <line x1="10" x2="10" y1="11" y2="17" />
    <line x1="14" x2="14" y1="11" y2="17" />
  </svg>
);

export const PinIcon: React.FC<IconProps> = ({ size = 14, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <line x1="12" y1="17" x2="12" y2="22" />
    <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.89A2 2 0 0 1 15 10.76V6h1a1 1 0 0 0 0-2H8a1 1 0 0 0 0 2h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.89A2 2 0 0 0 5 15.24Z" />
  </svg>
);

export const MoreHorizontalIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" />
  </svg>
);

export const AlertTriangleIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

/** Clean details/info icon representing contextual details and informational callouts. */
export const InfoIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <circle cx="12" cy="12" r="9.5" />
    <path d="M12 16v-4" />
    <circle cx="12" cy="8.25" r="0.9" fill="currentColor" stroke="none" />
  </svg>
);

/** Incognito glasses-and-hat icon indicating an unsaved temporary chat session. */
export const TemporaryChatIcon: React.FC<IconProps> = ({ size = 18, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M5.5 11 7.2 5.4A1.8 1.8 0 0 1 8.95 4.2l1.85.5a4.5 4.5 0 0 0 2.4 0l1.85-.5a1.8 1.8 0 0 1 1.75 1.2L18.5 11" />
    <path d="M3 11h18" />
    <circle cx="8" cy="16.75" r="3" />
    <circle cx="16" cy="16.75" r="3" />
    <path d="M11 16.5a1.25 1.25 0 0 1 2 0" />
  </svg>
);

/** Compose square-and-pen icon representing a fresh chat session. */
export const ComposeIcon: React.FC<IconProps> = ({ size = 16, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="M12 3H6a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3v-6" />
    <path d="M18.375 2.625a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.375-9.375Z" />
  </svg>
);

/** Compact dual-chevron indicator for footer account and role switching. */
export const ChevronsUpDownIcon: React.FC<IconProps> = ({ size = 14, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="m7 15 5 5 5-5" />
    <path d="m7 9 5-5 5 5" />
  </svg>
);

/** Downward chevron icon indicating interactive menus and scope pickers. */
export const ChevronDownIcon: React.FC<IconProps> = ({ size = 14, className = "", title, ...props }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden={!title}
    {...props}
  >
    {title && <title>{title}</title>}
    <path d="m6 9 6 6 6-6" />
  </svg>
);
