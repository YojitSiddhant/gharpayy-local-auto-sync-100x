import type { Metadata } from "next";
import { TowerAutoSyncPage } from "@/src/components/local-auto-sync/tower-auto-sync-page";
import Link from "next/link";
import { BarChart3 } from "@/src/components/ui/icons";

export const metadata: Metadata = { title: "Tower Auto Sync — Gharpayy", description: "Capture changed WhatsApp tab frames into Gharpayy local evidence review." };

export default function Home() {
  return <><nav className="flex justify-end border-b bg-[#fffaf7] px-4 py-3 sm:px-8"><Link href="/analytics" className="inline-flex items-center gap-2 rounded-lg border border-orange-200 bg-white px-3 py-2 text-sm font-semibold text-[#FF5533] shadow-sm transition hover:bg-orange-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#FF5533]"><BarChart3 className="size-4" />Analytics</Link></nav><TowerAutoSyncPage /></>;
}
