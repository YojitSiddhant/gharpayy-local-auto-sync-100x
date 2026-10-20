import type { Metadata } from "next";
import { LocalAutoSyncPanel } from "@/src/components/local-auto-sync/local-auto-sync-panel";

// Single page implementation shared by "/" and "/local-auto-sync" (no redirect, no duplicate UI).
export const localAutoSyncMetadata: Metadata = {
  title: "Gharpayy Local Auto Sync 100X — Screenshot review",
  description: "Read WhatsApp screenshots on your device with no external inference API and review local evidence.",
};

export function LocalAutoSyncPage() {
  return <LocalAutoSyncPanel />;
}
