import { afterEach, describe, expect, it, vi } from "vitest";
import { extractScreenshot, OcrRequestError, sniffImageType, validateAccountLabel, validateScreenshotFile } from "@/src/lib/ocr-api";
import { jsonResponse, PNG_BYTES, pngFile, sampleResponse } from "@/src/test-fixtures/ocr";

afterEach(() => vi.unstubAllGlobals());

describe("client-side screenshot validation", () => {
  it("accepts a PNG whose bytes match its type", async () => {
    expect(await validateScreenshotFile(pngFile())).toBeNull();
  });
  it("rejects unsupported types, empty files and spoofed signatures", async () => {
    expect(await validateScreenshotFile(new File(["x"], "a.gif", { type: "image/gif" }))).toMatch(/only PNG, JPEG or WebP/);
    expect(await validateScreenshotFile(new File([], "empty.png", { type: "image/png" }))).toMatch(/empty/);
    expect(await validateScreenshotFile(new File(["<html>"], "fake.png", { type: "image/png" }))).toMatch(/not a PNG, JPEG or WebP/);
    expect(await validateScreenshotFile(new File([PNG_BYTES], "wrong.jpg", { type: "image/jpeg" }))).toMatch(/does not match/);
  });
  it("rejects files over 10 MB", async () => {
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.png", { type: "image/png" });
    expect(await validateScreenshotFile(big)).toMatch(/exceeds 10 MB/);
  });
  it("sniffs WebP and JPEG signatures", () => {
    expect(sniffImageType(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe("image/jpeg");
    expect(sniffImageType(new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]))).toBe("image/webp");
  });
  it("requires a bounded account label", () => {
    expect(validateAccountLabel("  ")).toMatch(/Enter/);
    expect(validateAccountLabel("x".repeat(91))).toMatch(/at most 90/);
    expect(validateAccountLabel("Gharpayy WhatsApp")).toBeNull();
  });
});

describe("extractScreenshot", () => {
  it("posts raw bytes to the same-origin proxy with encoded headers and no secrets", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(sampleResponse, 201));
    vi.stubGlobal("fetch", fetchMock);
    const result = await extractScreenshot(pngFile("chat é.png"), " Gharpayy WhatsApp ");
    expect(result.rows[0].phone.normalized).toBe("+919876543210");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("/api/ocr/extract");
    expect(headers["x-account-label"]).toBe("Gharpayy%20WhatsApp");
    expect(headers["x-filename"]).toBe("chat%20%C3%A9.png");
    expect(headers["x-gharpayy-client"]).toBe("local-auto-sync");
    expect(Object.keys(headers).some((h) => /token|secret|key/i.test(h))).toBe(false);
  });
  it("surfaces deterministic backend errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "OCR_TIMEOUT", message: "OCR exceeded the processing time limit." } }, 504)));
    await expect(extractScreenshot(pngFile(), "A")).rejects.toMatchObject({ code: "OCR_TIMEOUT", status: 504 });
  });
  it("maps network failures and malformed responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(extractScreenshot(pngFile(), "A")).rejects.toMatchObject({ code: "NETWORK_ERROR" });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ rows: "nope" }, 200)));
    await expect(extractScreenshot(pngFile(), "A")).rejects.toBeInstanceOf(OcrRequestError);
  });
  it("rejects a response that claims verified identity", async () => {
    const tampered = { ...sampleResponse, rows: [{ ...sampleResponse.rows[0], identity_status: "verified" }] };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(tampered, 201)));
    await expect(extractScreenshot(pngFile(), "A")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });
});
