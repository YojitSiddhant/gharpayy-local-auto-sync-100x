import type { ExtractResponse } from "@/src/lib/ocr-api";
import type { EvidenceDetail, EvidenceStats, EvidenceSummary } from "@/src/lib/local-evidence";

export const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
export const ID = "11111111-2222-4333-8444-555555555555";

export function pngFile(name = "synthetic-chat.png") {
  return new File([PNG_BYTES], name, { type: "image/png" });
}

export function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, blob: async () => new Blob([JSON.stringify(body)]) } as unknown as Response;
}

export const sampleRows: ExtractResponse["rows"] = [
  {
    position: 1, display_header_text: "+91 98765 43210", phone: { raw: "+91 98765 43210", normalized: "+919876543210", status: "candidate" },
    message_preview: "Need a private room", preview_truncated: false, visible_timestamp_text: "2:52 pm", timestamp_kind: "clock_time",
    row_bounds: { left: 0, top: 180, right: 720, bottom: 260 }, header_bounds: { left: 140, top: 180, right: 380, bottom: 210 },
    ocr_confidence: 0.91, warnings: [], quarantined: false, identity_status: "unverified", review_status: "needs_review", sender_direction: "unknown", observed_name: null,
  },
  {
    position: 2, display_header_text: "+91 9876O 43210", phone: { raw: "+91 9876O 43210", normalized: null, status: "ambiguous" },
    message_preview: null, preview_truncated: false, visible_timestamp_text: null, timestamp_kind: null,
    row_bounds: { left: 0, top: 350, right: 720, bottom: 400 }, header_bounds: null, ocr_confidence: 0.6,
    warnings: ["Phone-like header contains OCR-confusable characters; digits not repaired"],
    quarantined: true, identity_status: "unverified", review_status: "needs_review", sender_direction: "unknown", observed_name: null,
  },
];

export const sampleResponse: ExtractResponse = {
  duplicate: false,
  capture: {
    id: ID, sha256: "a".repeat(64), account_label: "Gharpayy WhatsApp", original_filename: "synthetic-chat.png",
    mime_type: "image/png", byte_size: 20480, width: 720, height: 1280, source_mode: "manual-file", status: "completed",
    review_status: "unverified_review_only", cross_account_reuse: false, evidence_stored: true, received_at: "2026-10-20T10:00:00+00:00",
  },
  result: {
    id: "res-1", attempt: 1, status: "completed", engine: { name: "tesseract", version: "5.5.3", language: "eng", psm: 11 },
    preprocessing: ["grayscale"], processing_scale: 1, raw_text: "+91 98765 43210  2:52 pm\nNeed a private room", mean_confidence: 91.2,
    word_count: 8, line_count: 3, lines: [], warnings: [], parser_version: "phase2-chatlist-0.1", duration_ms: 1200, started_at: null, completed_at: null,
  },
  rows: sampleRows,
  notices: ["Unverified OCR evidence. Review against the original screenshot before any use."],
};

export const summary: EvidenceSummary = {
  id: ID, label: "synthetic-chat.png", file_name: "synthetic-chat.png", status: "review", trashed: false,
  account_label: "Gharpayy WhatsApp", captured_at: "2026-10-20T10:00:00+00:00",
};

export const stats: EvidenceStats = {
  review: 1, reviewed: 0, failed: 0, trash: 0, average_ms: 1200, records: 1, max_records: 200,
  stored_bytes: 20480, max_bytes: 104857600, duplicates: 0,
};

export const detail: EvidenceDetail = {
  ...summary,
  sha256: "a".repeat(64), mime_type: "image/png", byte_size: 20480, dimensions: { width: 720, height: 1280 },
  original_dimensions: { width: 720, height: 1280 }, crop: null, source_mode: "manual-file", source_label: null,
  screenshot_label: "synthetic-chat.png", received_at: "2026-10-20T10:00:00+00:00", review_status: "unverified_review_only",
  identity_status: "unverified", reviewed_at: null, reviewed_by: null, cross_account_reuse: false, duplicate_hits: 0, evidence_stored: true,
  error: null, text: "+91 98765 43210  2:52 pm\nNeed a private room", original_text: "+91 98765 43210  2:52 pm\nNeed a private room",
  corrections: [], trash: null, trash_history: [],
  result: { attempt: 1, status: "completed", engine: { name: "tesseract", version: "5.5.3", language: "eng", psm: 11 }, mean_confidence: 91.2, duration_ms: 1200, line_count: 3, lines: [{ text: "Need" }], warnings: [] },
  rows: sampleRows, conflicts: ["Row 2: Phone-like header contains OCR-confusable characters; digits not repaired"], quarantined: true,
  signals: [{ kind: "urgency", evidence: "urgent" }], proposals: [],
};
