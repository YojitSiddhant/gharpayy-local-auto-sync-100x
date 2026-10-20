import { afterEach, describe, expect, it, vi } from "vitest";
import { describeCaptureError, ExactFrameGate, queueErrorAction, sameBytes, startTicker, waitForVideoSize } from "@/src/lib/capture";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ExactFrameGate", () => {
  it("skips only byte-identical frames, never near-identical ones", () => {
    const gate = new ExactFrameGate();
    const a = new Uint8ClampedArray([1, 2, 3, 4]);
    expect(gate.unchanged(1, 1, a)).toBe(false);
    expect(gate.unchanged(1, 1, new Uint8ClampedArray([1, 2, 3, 4]))).toBe(true);
    expect(gate.unchanged(1, 1, new Uint8ClampedArray([1, 2, 3, 5]))).toBe(false); // one channel differs
    expect(gate.unchanged(2, 1, new Uint8ClampedArray(8))).toBe(false); // size change
    gate.reset();
    expect(gate.unchanged(2, 1, new Uint8ClampedArray(8))).toBe(false);
  });
});

describe("queueErrorAction", () => {
  it("never drops screenshots for transient or unknown failures", () => {
    for (const code of ["NETWORK_ERROR", "BACKEND_UNAVAILABLE", "OCR_ENGINE_UNAVAILABLE", "LOCAL_TOKEN_REQUIRED", "INVALID_RESPONSE", "UNKNOWN_ERROR", "STORAGE_FULL", "CAPTURE_IN_PROGRESS", "OCR_BUSY"]) {
      expect(queueErrorAction(code)).toBe("pause");
    }
    expect(queueErrorAction("CORRUPTED_IMAGE")).toBe("reject");
    expect(queueErrorAction("OCR_TIMEOUT")).toBe("recorded");
  });
});

describe("describeCaptureError", () => {
  it("explains permission denial and cancellation", () => {
    expect(describeCaptureError(new DOMException("Permission denied", "NotAllowedError"))).toMatch(/cancelled or not permitted/);
    expect(describeCaptureError(new DOMException("x", "NotReadableError"))).toMatch(/could not be read/);
    expect(describeCaptureError(new Error("other"))).toBe("other");
  });
});

describe("startTicker", () => {
  it("falls back to setInterval without Workers and stops cleanly", () => {
    vi.useFakeTimers();
    vi.stubGlobal("Worker", undefined);
    const tick = vi.fn();
    const ticker = startTicker(5000, tick);
    expect(ticker.kind).toBe("interval");
    vi.advanceTimersByTime(10_000);
    expect(tick).toHaveBeenCalledTimes(2);
    ticker.stop();
    vi.advanceTimersByTime(10_000);
    expect(tick).toHaveBeenCalledTimes(2);
  });

  it("uses a Worker timer when available and terminates it on stop", () => {
    const instances: { onmessage: (() => void) | null; postMessage: ReturnType<typeof vi.fn>; terminate: ReturnType<typeof vi.fn> }[] = [];
    vi.stubGlobal("Worker", class {
      onmessage: (() => void) | null = null;
      postMessage = vi.fn();
      terminate = vi.fn();
      constructor() { instances.push(this); }
    });
    URL.createObjectURL = vi.fn(() => "blob:ticker");
    URL.revokeObjectURL = vi.fn();
    const tick = vi.fn();
    const ticker = startTicker(5000, tick);
    expect(ticker.kind).toBe("worker");
    expect(instances[0].postMessage).toHaveBeenCalledWith(5000);
    instances[0].onmessage?.();
    expect(tick).toHaveBeenCalledTimes(1);
    ticker.stop();
    expect(instances[0].terminate).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:ticker");
  });
});

describe("waitForVideoSize", () => {
  it("resolves once metadata reports a size, or false on timeout", async () => {
    const video = document.createElement("video");
    let width = 0;
    Object.defineProperty(video, "videoWidth", { get: () => width });
    Object.defineProperty(video, "videoHeight", { get: () => width });
    const ready = waitForVideoSize(video, 1000);
    width = 640;
    video.dispatchEvent(new Event("loadedmetadata"));
    await expect(ready).resolves.toBe(true);
    width = 0;
    vi.useFakeTimers();
    const never = waitForVideoSize(video, 1000);
    vi.advanceTimersByTime(1000);
    await expect(never).resolves.toBe(false);
  });
});

describe("sameBytes", () => {
  it("is exact for aligned, unaligned and odd-length buffers", () => {
    const a = new Uint8ClampedArray([1, 2, 3, 4, 5, 6, 7]);
    expect(sameBytes(a, new Uint8ClampedArray([1, 2, 3, 4, 5, 6, 7]))).toBe(true);
    expect(sameBytes(a, new Uint8ClampedArray([1, 2, 3, 4, 5, 6, 8]))).toBe(false); // tail byte
    expect(sameBytes(a, new Uint8ClampedArray([1, 2, 9, 4, 5, 6, 7]))).toBe(false); // word byte
    expect(sameBytes(a, new Uint8ClampedArray(6))).toBe(false);
    const buffer = new Uint8ClampedArray(12).fill(3);
    expect(sameBytes(buffer.subarray(1, 9), new Uint8ClampedArray(8).fill(3))).toBe(true); // unaligned view
    expect(sameBytes(buffer.subarray(1, 9), new Uint8ClampedArray(8).fill(4))).toBe(false);
  });
});
