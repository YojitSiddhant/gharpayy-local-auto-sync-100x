import type { Metadata } from "next";
import Link from "next/link";
import { TowerAutoSyncPage } from "@/src/components/local-auto-sync/tower-auto-sync-page";

export const metadata: Metadata = { title: "Tower Auto Sync — Gharpayy", description: "Capture changed WhatsApp tab frames into Gharpayy local evidence review." };

export default function TowerAutoSyncRoute() {
  return <><nav className="flex justify-end border-b bg-[#fffaf7] px-4 py-3 sm:px-8"><Link href="/analytics" className="rounded-lg border border-orange-200 px-3 py-2 text-sm font-semibold text-[#FF5533] hover:bg-orange-50">OCR & ML Analytics</Link></nav><TowerAutoSyncPage /></>;
}
