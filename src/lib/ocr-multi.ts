export const OCR_MODES = ["LOCAL_ONLY", "SINGLE_PROVIDER", "MULTI_PROVIDER"] as const;
export type OcrMode = (typeof OCR_MODES)[number];
export type EngineName = "tesseract" | "gemini" | "groq";
export type NormalizedOcr = { provider: EngineName; text: string; lines: Array<{ text: string; box?: { left: number; top: number; right: number; bottom: number }; confidence: number | null }>; durationMs: number; status: "completed" | "failed"; error?: "rate_limit" | "quota" | "timeout" | "provider_error" | "consent_required" };
export type Comparison = { primary: NormalizedOcr; results: NormalizedOcr[]; disagreements: string[]; fallbackUsed: boolean };
export type OcrRunner = (input: { bytes: Uint8Array; credential?: string; signal: AbortSignal }) => Promise<NormalizedOcr>;
export type MultiConfig = { mode: OcrMode; selectedProvider?: "gemini" | "groq"; enabled: Record<EngineName, boolean>; configured: Record<EngineName, boolean>; externalOptIn: boolean; budgetRemaining: number; timeoutMs?: number; concurrency?: number };
const limit = async <T>(tasks: (() => Promise<T>)[], concurrency: number) => { const out: T[] = []; let cursor = 0; async function worker() { while (cursor < tasks.length) { const i = cursor++; out[i] = await tasks[i](); } } await Promise.all(Array.from({ length: Math.min(concurrency, tasks.length) }, worker)); return out; };
export async function runOcrEngines(input: { bytes: Uint8Array; config: MultiConfig; credentials: Partial<Record<"gemini" | "groq", string>>; runners: Record<EngineName, OcrRunner> }): Promise<Comparison> {
  const { config } = input; const local = config.enabled.tesseract && config.configured.tesseract;
  const cloud: EngineName[] = (["gemini", "groq"] as EngineName[]).filter((p) => config.enabled[p] && config.configured[p]);
  if (cloud.length && !config.externalOptIn) { const blocked = cloud.map((provider) => ({ provider, text: "", lines: [], durationMs: 0, status: "failed" as const, error: "consent_required" as const })); const primary = await input.runners.tesseract({ bytes: input.bytes, signal: AbortSignal.timeout(config.timeoutMs ?? 20_000) }); return { primary, results: [primary, ...blocked], disagreements: ["External OCR was blocked because consent is disabled."], fallbackUsed: true }; }
  let providers: EngineName[] = config.mode === "LOCAL_ONLY" ? ["tesseract"] : config.mode === "SINGLE_PROVIDER" ? [config.selectedProvider ?? "tesseract"] : ["tesseract", ...cloud];
  providers = providers.filter((p) => p === "tesseract" ? local : cloud.includes(p)).slice(0, Math.max(1, config.budgetRemaining));
  const results = await limit(providers.map((provider) => async () => { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 20_000); try { return await input.runners[provider]({ bytes: input.bytes, credential: input.credentials[provider as "gemini" | "groq"], signal: controller.signal }); } catch (error) { const name = error instanceof Error && error.name === "AbortError" ? "timeout" : "provider_error"; return { provider, text: "", lines: [], durationMs: 0, status: "failed" as const, error: name as "timeout" | "provider_error" }; } finally { clearTimeout(timer); } }), config.concurrency ?? 2);
  const primary = results.find((r) => r.provider === "tesseract" && r.status === "completed") ?? results.find((r) => r.status === "completed") ?? results[0];
  const disagreements = results.filter((r) => r.status === "completed" && r.text !== primary.text).map((r) => `${r.provider} differs from ${primary.provider}; local/default result retained.`);
  return { primary, results, disagreements, fallbackUsed: primary.provider !== "tesseract" || results.some((r) => r.status === "failed") };
}
