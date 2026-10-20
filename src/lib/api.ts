import { z } from "zod";

const healthSchema = z.object({ status: z.string(), service: z.string() });
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function fetchBackendHealth(signal?: AbortSignal) {
  const response = await fetch(`${apiBaseUrl}/health`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  return healthSchema.parse(await response.json());
}
