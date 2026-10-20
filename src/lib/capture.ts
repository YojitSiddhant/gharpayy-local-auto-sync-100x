/** Capture and queue reliability helpers for the Local Auto Sync panel. No UI. */

export const CAPTURE_INTERVAL_MS = 5000;
/** Reference capture policy: a gap this long between sampling ticks requires re-approval. */
export const CAPTURE_GAP_MS = 15000;

export type Ticker = { stop: () => void; kind: "worker" | "interval" };

/**
 * Fire `onTick` every `intervalMs`. Prefers a dedicated Worker timer because Chrome heavily throttles
 * main-thread timers in background tabs (the operator usually switches to the shared WhatsApp window).
 * Falls back to window.setInterval when Workers are unavailable.
 */
export function startTicker(intervalMs: number, onTick: () => void): Ticker {
  if (typeof Worker !== "undefined" && typeof Blob !== "undefined" && typeof URL.createObjectURL === "function") {
    let url = "";
    try {
      url = URL.createObjectURL(new Blob(["let t=null;onmessage=e=>{clearInterval(t);if(e.data>0)t=setInterval(()=>postMessage(0),e.data)}"], { type: "text/javascript" }));
      const worker = new Worker(url);
      worker.onmessage = () => onTick();
      worker.postMessage(intervalMs);
      return {
        kind: "worker",
        stop: () => {
          worker.postMessage(0);
          worker.terminate();
          URL.revokeObjectURL(url);
        },
      };
    } catch {
      if (url) URL.revokeObjectURL(url);
    }
  }
  const id = window.setInterval(onTick, intervalMs);
  return { kind: "interval", stop: () => window.clearInterval(id) };
}

/**
 * Exact RGBA change gate (reference edge-change-gate.js): a frame is skipped only when every pixel is
 * identical to the last frame sent. No perceptual threshold, so small text changes are never ignored.
 */
export class ExactFrameGate {
  private previous: Uint8ClampedArray | null = null;
  private width = 0;
  private height = 0;

  /** Returns true when the frame is byte-identical to the previous accepted frame. */
  unchanged(width: number, height: number, pixels: Uint8ClampedArray): boolean {
    const same = this.previous !== null && this.width === width && this.height === height && sameBytes(this.previous, pixels);
    if (!same) {
      this.previous = new Uint8ClampedArray(pixels);
      this.width = width;
      this.height = height;
    }
    return same;
  }

  reset() {
    this.previous = null;
    this.width = this.height = 0;
  }
}

/**
 * Exact byte equality, compared four bytes at a time (≈30–40× faster than Array.prototype.every on a
 * 1920×1080 RGBA frame; see docs/PHASE_4_HARDENING.md). Identical semantics: any differing byte → false.
 */
export function sameBytes(a: Uint8ClampedArray, b: Uint8ClampedArray): boolean {
  if (a.length !== b.length) return false;
  const words = a.length >> 2;
  if (a.byteOffset % 4 === 0 && b.byteOffset % 4 === 0) {
    const x = new Uint32Array(a.buffer, a.byteOffset, words);
    const y = new Uint32Array(b.buffer, b.byteOffset, words);
    for (let i = 0; i < words; i++) if (x[i] !== y[i]) return false;
  } else {
    for (let i = 0; i < words << 2; i++) if (a[i] !== b[i]) return false;
  }
  for (let i = words << 2; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Upper bound on screenshot bytes held in the in-browser queue (100 items × up to 10 MB would be ~1 GB). */
export const LOCAL_QUEUE_BYTE_LIMIT = 200 * 1024 * 1024;

/** File-specific rejections: retrying the same bytes can never succeed. */
const REJECTED = new Set([
  "UNSUPPORTED_MEDIA_TYPE", "INVALID_SIGNATURE", "SIGNATURE_MISMATCH", "CORRUPTED_IMAGE", "IMAGE_TOO_SMALL",
  "IMAGE_DIMENSIONS_TOO_LARGE", "UPLOAD_TOO_LARGE", "EMPTY_UPLOAD", "ANIMATED_IMAGE_NOT_SUPPORTED", "INVALID_HEADER", "ACCOUNT_LABEL_REQUIRED",
]);
/** The backend saved a failed reading for this screenshot; it can be retried from the evidence view. */
const RECORDED = new Set(["OCR_TIMEOUT", "OCR_FAILED"]);

/**
 * What the queue does after an upload error. Anything not known to be file-specific or already recorded
 * keeps the screenshot and pauses the queue, so transient or systemic failures never lose screenshots.
 */
export function queueErrorAction(code: string): "reject" | "recorded" | "pause" {
  if (REJECTED.has(code)) return "reject";
  if (RECORDED.has(code)) return "recorded";
  return "pause";
}

export function describeCaptureError(error: unknown): string {
  const name = error instanceof DOMException || error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "AbortError") return "Screen sharing was cancelled or not permitted. Nothing was captured.";
  if (name === "NotFoundError") return "No shareable screen, window or tab was found.";
  if (name === "NotReadableError") return "The selected source could not be read. Close other capture apps and try again.";
  return error instanceof Error ? error.message : "Screen sharing failed";
}

/** Wait until a <video> fed by a MediaStream reports its frame size (it can be 0 right after play()). */
export function waitForVideoSize(element: HTMLVideoElement, timeoutMs = 3000): Promise<boolean> {
  if (element.videoWidth > 0 && element.videoHeight > 0) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = () => {
      element.removeEventListener("loadedmetadata", check);
      element.removeEventListener("resize", check);
      window.clearTimeout(timer);
      resolve(element.videoWidth > 0 && element.videoHeight > 0);
    };
    const check = () => {
      if (element.videoWidth > 0 && element.videoHeight > 0) done();
    };
    const timer = window.setTimeout(done, timeoutMs);
    element.addEventListener("loadedmetadata", check);
    element.addEventListener("resize", check);
  });
}
