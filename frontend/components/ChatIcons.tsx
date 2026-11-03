import type { SVGProps } from "react";

export type ChatIconName =
  | "panel-left" | "library" | "file" | "copy" | "refresh" | "up" | "down" | "menu"
  | "chevron" | "close" | "external" | "send" | "stop" | "search" | "check";

const paths: Record<ChatIconName, string[]> = {
  "panel-left": ["M3 3h18v18H3z", "M9 3v18"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  library: ["M4 19.5A2.5 2.5 0 0 1 6.5 17H20", "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M8 13h8", "M8 17h8"],
  copy: ["M8 8V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4", "M4 6h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"],
  refresh: ["M20 7v5h-5", "M4 17v-5h5", "M5.6 9A7 7 0 0 1 18.3 6L20 12", "M4 12l1.7 6A7 7 0 0 0 18.4 15"],
  up: ["M7 10v12", "M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.3 8a2 2 0 0 1-2 1.7H7V10l4.7-7a2 2 0 0 1 3.3 2.9z", "M7 10H4a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"],
  down: ["M7 14V2", "M15 18.1 14 14h5.8a2 2 0 0 0 2-2.3l-1.3-8a2 2 0 0 0-2-1.7H7v12l4.7 7a2 2 0 0 0 3.3-2.9z", "M7 14H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h3"],
  chevron: ["m6 9 6 6 6-6"],
  close: ["M18 6 6 18", "m6 6 12 12"],
  external: ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  send: ["m22 2-7 20-4-9-9-4z", "M22 2 11 13"],
  stop: ["M5 5h14v14H5z"],
  search: ["m21 21-4.3-4.3", "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z"],
  check: ["m5 12 4 4L19 6"],
};

export function ChatIcon({
  name,
  size = 16,
  ...props
}: SVGProps<SVGSVGElement> & { name: ChatIconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {paths[name].map((path) => <path key={path} d={path} />)}
    </svg>
  );
}
