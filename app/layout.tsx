import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gharpayy Local Auto Sync 100X",
  description: "Phase 1 foundation for local-first Gharpayy operations.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
