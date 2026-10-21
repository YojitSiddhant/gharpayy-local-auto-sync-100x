import type { ReactNode, SVGProps } from "react";

// Geometry from Lucide (ISC licence), the icon set used by the reference page.
function Icon({ children, className = "size-4", ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} {...props}>
      {children}
    </svg>
  );
}

type P = { className?: string };
export const Upload = (p: P) => <Icon {...p}><path d="M12 3v12" /><path d="m17 8-5-5-5 5" /><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /></Icon>;
export const MonitorUp = (p: P) => <Icon {...p}><path d="m9 10 3-3 3 3" /><path d="M12 13V7" /><rect width="20" height="14" x="2" y="3" rx="2" /><path d="M12 17v4" /><path d="M8 21h8" /></Icon>;
export const BarChart3 = (p: P) => <Icon {...p}><path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M3 20h16" /></Icon>;
export const Pause = (p: P) => <Icon {...p}><rect x="14" y="3" width="5" height="18" rx="1" /><rect x="5" y="3" width="5" height="18" rx="1" /></Icon>;
export const Play = (p: P) => <Icon {...p}><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z" /></Icon>;
export const Square = (p: P) => <Icon {...p}><rect width="18" height="18" x="3" y="3" rx="2" /></Icon>;
export const Download = (p: P) => <Icon {...p}><path d="M12 15V3" /><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5" /></Icon>;
export const Search = (p: P) => <Icon {...p}><path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" /></Icon>;
export const Trash2 = (p: P) => <Icon {...p}><path d="M10 11v6" /><path d="M14 11v6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M3 6h18" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></Icon>;
export const RotateCcw = (p: P) => <Icon {...p}><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /></Icon>;
export const Save = (p: P) => <Icon {...p}><path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" /><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7" /><path d="M7 3v4a1 1 0 0 0 1 1h7" /></Icon>;
export const Check = (p: P) => <Icon {...p}><path d="M20 6 9 17l-5-5" /></Icon>;
export const Sparkles = (p: P) => <Icon {...p}><path d="m12 3-1.2 5.8L5 10l5.8 1.2L12 17l1.2-5.8L19 10l-5.8-1.2z" /><path d="m19 16-.6 2.4L16 19l2.4.6L19 22l.6-2.4L22 19l-2.4-.6z" /></Icon>;
