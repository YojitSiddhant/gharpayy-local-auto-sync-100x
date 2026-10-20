import type { Metadata } from "next";
import { TowerAutoSyncPage } from "@/src/components/local-auto-sync/tower-auto-sync-page";
import { ZoneControl } from "./zone-control";

export const metadata: Metadata = { title: "Tower Auto Sync — Gharpayy", description: "Capture changed WhatsApp tab frames into Gharpayy local evidence review." };

export default function TowerAutoSyncRoute() {
  return <><ZoneControl /><TowerAutoSyncPage /></>;
}
