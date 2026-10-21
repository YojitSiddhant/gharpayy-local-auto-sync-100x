import { z } from "zod";
import { CLIENT_HEADER, CLIENT_HEADER_VALUE, errorSchema, OcrRequestError, rowSchema } from "@/src/lib/ocr-api";

export const LOCAL_API = "/api/local/evidence";
export const STATUS_FILTERS = ["all", "review", "reviewed", "error", "trash"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];
export type Bounds = { x0: number; y0: number; x1: number; y1: number };

const statusSchema = z.enum(["processing", "review", "reviewed", "error"]);
const fieldVerificationSchema = z.object({ field_name: z.enum(["phone", "message", "timestamp"]), observed_value: z.string().nullable(), verified_value: z.string().nullable(), status: z.enum(["verified", "missing", "unreadable", "incorrect"]), dataset_split: z.enum(["unassigned", "training", "evaluation"]), approved_for_dataset: z.boolean(), reviewer: z.string(), revision: z.number() });

export const summarySchema = z.object({
  id: z.string(),
  label: z.string(),
  file_name: z.string().nullable(),
  status: statusSchema,
  trashed: z.boolean(),
  account_label: z.string(),
  captured_at: z.string().nullable(),
});

export const statsSchema = z.object({
  review: z.number(), reviewed: z.number(), failed: z.number(), trash: z.number(), average_ms: z.number(),
  records: z.number(), max_records: z.number(), stored_bytes: z.number(), max_bytes: z.number(), duplicates: z.number(),
});

const dims = z.object({ width: z.number(), height: z.number() });
const boundsSchema = z.object({ x0: z.number(), y0: z.number(), x1: z.number(), y1: z.number() });

export const detailSchema = summarySchema.extend({
  sha256: z.string(),
  mime_type: z.string(),
  byte_size: z.number(),
  dimensions: dims,
  original_dimensions: z.object({ width: z.number().nullable(), height: z.number().nullable() }).nullable(),
  crop: boundsSchema.nullable(),
  source_mode: z.string(),
  source_label: z.string().nullable(),
  screenshot_label: z.string().nullable(),
  received_at: z.string().nullable(),
  revision: z.number().int().nonnegative(),
  review_status: z.literal("unverified_review_only"),
  identity_status: z.literal("unverified"),
  reviewed_at: z.string().nullable(),
  reviewed_by: z.string().nullable(),
  cross_account_reuse: z.boolean(),
  duplicate_hits: z.number(),
  evidence_stored: z.boolean(),
  error: z.object({ code: z.string().nullable(), message: z.string().nullable() }).nullable(),
  text: z.string(),
  original_text: z.string(),
  corrections: z.array(z.object({ time: z.string().nullable(), old_text: z.string().nullable(), new_text: z.string(), reviewer: z.string() })),
  trash: z.object({ time: z.string().nullable(), reason: z.string().nullable(), reviewer: z.string().nullable() }).nullable(),
  trash_history: z.array(z.object({ action: z.string(), time: z.string().nullable(), reason: z.string().nullable(), reviewer: z.string() })),
  result: z.object({
    attempt: z.number(),
    status: z.enum(["completed", "failed"]),
    engine: z.object({ name: z.string(), version: z.string().nullable(), language: z.string(), psm: z.number() }),
    mean_confidence: z.number().nullable(),
    duration_ms: z.number().nullable(),
    line_count: z.number(),
    lines: z.array(z.record(z.string(), z.unknown())),
    warnings: z.array(z.string()),
  }).passthrough().nullable(),
  rows: z.array(rowSchema.extend({ field_verifications: z.array(fieldVerificationSchema).optional() })),
  conflicts: z.array(z.string()),
  quarantined: z.boolean(),
  signals: z.array(z.object({ kind: z.string(), evidence: z.string() })),
  proposals: z.array(z.object({
    kind: z.enum(["label", "unread"]), text: z.string(), bounds: boundsSchema, color: z.string().nullable(),
    color_meaning: z.literal("unknown"), count: z.number().nullable(), status: z.literal("tentative"),
  })),
  cross_zone_duplicate: z.object({ possible: z.boolean(), zone_count: z.number(), zones: z.array(z.string()), observations: z.array(z.object({ capture_id: z.string(), zone_name: z.string(), captured_at: z.string().nullable(), position: z.number(), status: z.string(), quarantined: z.boolean() })) }).optional(),
  possible_duplicate: z.boolean().optional(),
});

export type EvidenceSummary = z.infer<typeof summarySchema>;
export type EvidenceStats = z.infer<typeof statsSchema>;
export type EvidenceDetail = z.infer<typeof detailSchema>;

async function localFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${LOCAL_API}${path}`, {
      ...init,
      cache: "no-store",
      credentials: "same-origin",
      headers: { ...(init.body ? { "content-type": "application/json" } : {}), [CLIENT_HEADER]: CLIENT_HEADER_VALUE },
    });
  } catch {
    throw new OcrRequestError("NETWORK_ERROR", "Could not reach the local server. Is `npm run dev` running?");
  }
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      /* fall through */
    }
    const parsed = errorSchema.safeParse(payload);
    if (parsed.success) throw new OcrRequestError(parsed.data.error.code, parsed.data.error.message, response.status);
    throw new OcrRequestError("UNKNOWN_ERROR", `Request failed (${response.status}).`, response.status);
  }
  return response;
}

async function parsed<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  const result = schema.safeParse(await response.json().catch(() => null));
  if (!result.success) throw new OcrRequestError("INVALID_RESPONSE", "The local server returned an unexpected response.", response.status);
  return result.data;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export async function listEvidence(search: string, status: StatusFilter) {
  const query = new URLSearchParams({ status });
  if (search.trim()) query.set("search", search.trim());
  return (await parsed(await localFetch(`?${query}`), z.object({ items: z.array(summarySchema) }))).items;
}
export async function listEvidencePage(search: string, status: StatusFilter, page = 1, pageSize = 25) {
  const query = new URLSearchParams({ status, page: String(page), page_size: String(pageSize) });
  if (search.trim()) query.set("search", search.trim());
  return parsed(await localFetch(`?${query}`), z.object({ items: z.array(summarySchema), page: z.number(), page_size: z.number(), has_more: z.boolean() }));
}
export async function getStats() {
  return parsed(await localFetch("/stats"), statsSchema);
}
export async function getEvidence(id: string) {
  return parsed(await localFetch(`/${id}`), detailSchema);
}
export async function saveFieldVerification(id: string, body: { position: number; field_name: "phone" | "message" | "timestamp"; status: "verified" | "missing" | "unreadable" | "incorrect"; verified_value?: string | null; dataset_split: "unassigned" | "training" | "evaluation"; approved_for_dataset: boolean; reviewer: string; expected_revision?: number }) {
  return parsed(await localFetch(`/${id}/field-verifications`, post(body)), detailSchema);
}
/** Load the protected screenshot as a same-origin object URL (never a public file path). */
export async function getEvidenceImageUrl(id: string) {
  const blob = await (await localFetch(`/${id}/image`)).blob();
  return URL.createObjectURL(blob);
}
/** `expected_revision` is the revision the edit was based on; a stale one is rejected with EDIT_CONFLICT (409). */
export async function updateEvidence(id: string, body: { expected_revision: number; reviewer?: string; screenshot_label?: string; text?: string; observed_names?: { position: number; name: string }[] }, keepalive = false) {
  // keepalive lets a save started during page unload (pagehide) complete after the tab closes.
  return parsed(await localFetch(`/${id}`, { method: "PATCH", body: JSON.stringify(body), keepalive }), detailSchema);
}
export async function markReviewed(id: string, reviewer: string, expectedRevision: number) {
  return parsed(await localFetch(`/${id}/review`, post({ reviewer, expected_revision: expectedRevision })), detailSchema);
}
export async function setTrashed(id: string, reason: string, reviewer: string, restore: boolean) {
  return parsed(await localFetch(`/${id}/${restore ? "restore" : "trash"}`, post({ reason, reviewer })), detailSchema);
}
export async function retryOcr(id: string, reviewer: string) {
  return parsed(await localFetch(`/${id}/retry`, post({ reviewer })), detailSchema);
}
export async function proposeRegion(id: string, kind: "label" | "unread", region: Bounds) {
  return parsed(await localFetch(`/${id}/proposals`, post({ kind, region })), detailSchema);
}
export async function purgeTrash(reviewer: string) {
  return parsed(await localFetch("/purge-trash", post({ confirm: true, reviewer })), z.object({ purged: z.number() }));
}
export async function exportEvidence(): Promise<Blob> {
  return (await localFetch("/export")).blob();
}

/** Reference captureMatches: same source size and a crop strictly inside it. */
export function captureMatches(width: number, height: number, expected: { width: number; height: number }, crop: Bounds) {
  return width === expected.width && height === expected.height && crop.x0 >= 0 && crop.y0 >= 0 && crop.x1 <= width && crop.y1 <= height && crop.x1 > crop.x0 && crop.y1 > crop.y0;
}
