/** One icon family, one stroke width, one size token — mixing sets or weights
 *  is the fastest way to make an interface look assembled rather than designed.
 *  Drawn inline so the app stays self-contained with no icon-font download. */

type Props = { className?: string };

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function IconDashboard({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  );
}

export function IconQueue({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M3 6h13M3 12h13M3 18h9" />
      <circle cx="20" cy="6" r="1.6" />
      <circle cx="20" cy="12" r="1.6" />
    </svg>
  );
}

export function IconCases({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7" />
      <path d="M9 13.5l2 2 4-4" />
    </svg>
  );
}

export function IconBaselines({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M3 20V4" />
      <path d="M3 16h18" strokeDasharray="3 3" />
      <path d="M6 16V12M11 16V8M16 16V10M21 16V6" />
    </svg>
  );
}

export function IconModel({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4l2.5 2.5" />
    </svg>
  );
}

export function IconAlert({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M12 4.5 2.8 20h18.4L12 4.5Z" />
      <path d="M12 10v4M12 17.2v.1" />
    </svg>
  );
}

export function IconMerchants({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M4 9h16l-1 11H5L4 9Z" />
      <path d="M9 9V6a3 3 0 0 1 6 0v3" />
    </svg>
  );
}

export function IconShield({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M12 3 5 6v6c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6l-7-3Z" />
      <path d="M9.5 12l1.8 1.8L15 10" />
    </svg>
  );
}

export function IconClock({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 1.8" />
    </svg>
  );
}

export function IconChevron({ className = "h-4 w-4" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

export function IconSettings({ className = "h-5 w-5" }: Props) {
  return (
    <svg {...base} className={className}>
      <path d="M4 8h10M18 8h2M4 16h4M12 16h8" />
      <circle cx="16" cy="8" r="2.2" />
      <circle cx="10" cy="16" r="2.2" />
    </svg>
  );
}
