import { z } from "zod";

export const OCR_PROXY_PATH = "/api/ocr/extract";
export const CLIENT_HEADER = "x-gharpayy-client";
export const CLIENT_HEADER_VALUE = "local-auto-sync";
export const ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_ACCOUNT_LABEL = 90;

const bounds = z.object({ left: z.number(), top: z.number(), right: z.number(), bottom: z.number() });

export const rowSchema = z.object({
  position: z.number(),
  display_header_text: z.string().nullable(),
  phone: z.object({ raw: z.string().nullable(), normalized: z.string().nullable(), status: z.enum(["candidate", "ambiguous", "conflict", "none"]) }),
  message_preview: z.string().nullable(),
  preview_truncated: z.boolean(),
  visible_timestamp_text: z.string().nullable(),
  timestamp_kind: z.string().nullable(),
  row_bounds: bounds,
  header_bounds: bounds.nullable(),
  ocr_confidence: z.number().nullable(),
  warnings: z.array(z.string()),
  quarantined: z.boolean(),
  identity_status: z.literal("unverified"),
  review_status: z.literal("needs_review"),
  sender_direction: z.literal("unknown"),
  observed_name: z.string().nullable().optional(),
});

export const extractResponseSchema = z.object({
  duplicate: z.boolean(),
  capture: z.object({
    id: z.string(),
    sha256: z.string(),
    account_label: z.string(),
    original_filename: z.string().nullable(),
    mime_type: z.string(),
    byte_size: z.number(),
    width: z.number(),
    height: z.number(),
    source_mode: z.string(),
    status: z.enum(["processing", "completed", "failed"]),
    review_status: z.literal("unverified_review_only"),
    cross_account_reuse: z.boolean(),
    evidence_stored: z.boolean(),
    trashed: z.boolean().optional(),
    received_at: z.string().nullable(),
  }),
  result: z.object({
    id: z.string(),
    attempt: z.number(),
    status: z.enum(["completed", "failed"]),
    engine: z.object({ name: z.string(), version: z.string().nullable(), language: z.string(), psm: z.number() }),
    preprocessing: z.array(z.string()),
    processing_scale: z.number().nullable(),
    raw_text: z.string().nullable(),
    mean_confidence: z.number().nullable(),
    word_count: z.number(),
    line_count: z.number(),
    lines: z.array(z.record(z.string(), z.unknown())),
    warnings: z.array(z.string()),
    parser_version: z.string().nullable(),
    duration_ms: z.number().nullable(),
    started_at: z.string().nullable(),
    completed_at: z.string().nullable(),
  }).nullable(),
  rows: z.array(rowSchema),
  notices: z.array(z.string()),
});

export const errorSchema = z.object({ error: z.object({ code: z.string(), message: z.string() }) });

export type ExtractResponse = z.infer<typeof extractResponseSchema>;
export type RowObservation = z.infer<typeof rowSchema>;

export class OcrRequestError extends Error {
  constructor(public readonly code: string, message: string, public readonly status?: number) {
    super(message);
    this.name = "OcrRequestError";
  }
}

async function readHead(file: Blob, length: number): Promise<Uint8Array> {
  const slice = file.slice(0, length);
  if (typeof slice.arrayBuffer === "function") return new Uint8Array(await slice.arrayBuffer());
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(new Error("Could not read file"));
    reader.readAsArrayBuffer(slice);
  });
}

export function sniffImageType(head: Uint8Array): string | null {
  const starts = (bytes: number[], offset = 0) => bytes.every((b, i) => head[offset + i] === b);
  if (starts([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return "image/png";
  if (starts([0xff, 0xd8, 0xff])) return "image/jpeg";
  if (starts([0x52, 0x49, 0x46, 0x46]) && starts([0x57, 0x45, 0x42, 0x50], 8)) return "image/webp";
  return null;
}

/** Fast client-side pre-check. The backend re-validates everything; this only gives early feedback. */
export async function validateScreenshotFile(file: File): Promise<string | null> {
  if (!(ALLOWED_IMAGE_TYPES as readonly string[]).includes(file.type)) return `${file.name}: only PNG, JPEG or WebP screenshots are accepted.`;
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name}: NOT accepted; exceeds 10 MB.`;
  const sniffed = sniffImageType(await readHead(file, 12));
  if (sniffed === null) return `${file.name}: file content is not a PNG, JPEG or WebP image.`;
  if (sniffed !== file.type) return `${file.name}: file content does not match its declared image type.`;
  return null;
}

export function validateAccountLabel(label: string): string | null {
  const value = label.trim();
  if (!value) return "Enter a WhatsApp account label.";
  if (value.length > MAX_ACCOUNT_LABEL) return `Account label must be at most ${MAX_ACCOUNT_LABEL} characters.`;
  return null;
}

export type CaptureMetadata = {
  sourceMode: "manual-file" | "screen-capture";
  capturedAt: string;
  sourceLabel?: string;
  originalDimensions?: { width: number; height: number };
  crop?: { x0: number; y0: number; x1: number; y1: number };
  captureSessionId?: string;
};

function metadataHeaders(meta?: CaptureMetadata): Record<string, string> {
  if (!meta) return {};
  const headers: Record<string, string> = { "x-source-mode": meta.sourceMode, "x-captured-at": meta.capturedAt };
  if (meta.sourceLabel) headers["x-source-label"] = encodeURIComponent(meta.sourceLabel.slice(0, 255));
  if (meta.originalDimensions) {
    headers["x-original-width"] = String(meta.originalDimensions.width);
    headers["x-original-height"] = String(meta.originalDimensions.height);
  }
  if (meta.crop) headers["x-crop"] = [meta.crop.x0, meta.crop.y0, meta.crop.x1, meta.crop.y1].join(",");
  if (meta.captureSessionId) headers["x-capture-session-id"] = meta.captureSessionId;
  return headers;
}

export async function extractScreenshot(file: File, accountLabel: string, signal?: AbortSignal, meta?: CaptureMetadata): Promise<ExtractResponse> {
  let response: Response;
  try {
    response = await fetch(OCR_PROXY_PATH, {
      method: "POST",
      body: file,
      signal,
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        "content-type": file.type,
        "x-account-label": encodeURIComponent(accountLabel.trim()),
        "x-filename": encodeURIComponent(file.name.slice(0, 255)),
        ...metadataHeaders(meta),
        [CLIENT_HEADER]: CLIENT_HEADER_VALUE,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new OcrRequestError("NETWORK_ERROR", "Could not reach the local server. Is `npm run dev` running?");
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new OcrRequestError("INVALID_RESPONSE", "The local server returned an unreadable response.", response.status);
  }
  if (!response.ok) {
    const parsed = errorSchema.safeParse(payload);
    if (parsed.success) throw new OcrRequestError(parsed.data.error.code, parsed.data.error.message, response.status);
    throw new OcrRequestError("UNKNOWN_ERROR", `Request failed (${response.status}).`, response.status);
  }
  const parsed = extractResponseSchema.safeParse(payload);
  if (!parsed.success) throw new OcrRequestError("INVALID_RESPONSE", "The OCR response did not match the expected format.", response.status);
  return parsed.data;
}
