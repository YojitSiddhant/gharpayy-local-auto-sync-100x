import { describe, expect, it } from "vitest";
import { checkLocalRequest, checkProxyRequest, readCappedBody, resolveEvidencePath } from "@/src/lib/ocr-proxy";

function headers(overrides: Record<string, string | null> = {}) {
  const base: Record<string, string> = {
    host: "localhost:3000",
    origin: "http://localhost:3000",
    "sec-fetch-site": "same-origin",
    "x-gharpayy-client": "local-auto-sync",
    "content-type": "image/png",
    "content-length": "1024",
  };
  const h = new Headers();
  for (const [k, v] of Object.entries({ ...base, ...overrides })) if (v !== null) h.set(k, v);
  return h;
}

describe("proxy request guard", () => {
  it("accepts a same-origin loopback browser upload", () => {
    expect(checkProxyRequest(headers())).toBeNull();
  });
  it("rejects other websites (CSRF) and missing origins", () => {
    expect(checkProxyRequest(headers({ origin: "https://evil.example" }))?.code).toBe("ORIGIN_NOT_ALLOWED");
    expect(checkProxyRequest(headers({ origin: null }))?.code).toBe("ORIGIN_NOT_ALLOWED");
    expect(checkProxyRequest(headers({ "sec-fetch-site": "cross-site" }))?.code).toBe("ORIGIN_NOT_ALLOWED");
  });
  it("rejects non-loopback hosts (DNS rebinding)", () => {
    expect(checkProxyRequest(headers({ host: "attacker.example:3000", origin: "http://attacker.example:3000" }))?.code).toBe("HOST_NOT_ALLOWED");
  });
  it("requires the client header, an image type and a bounded length", () => {
    expect(checkProxyRequest(headers({ "x-gharpayy-client": null }))?.code).toBe("CLIENT_HEADER_REQUIRED");
    expect(checkProxyRequest(headers({ "content-type": "text/plain" }))?.status).toBe(415);
    expect(checkProxyRequest(headers({ "content-length": String(11 * 1024 * 1024) }))?.status).toBe(413);
  });
  it("stops reading bodies beyond the cap", async () => {
    const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new Uint8Array(8)); c.enqueue(new Uint8Array(8)); c.close(); } });
    expect(await readCappedBody(stream, 10)).toBeNull();
    const ok = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new Uint8Array([1, 2])); c.close(); } });
    expect(Array.from((await readCappedBody(ok, 10)) ?? [])).toEqual([1, 2]);
  });
});

describe("evidence proxy allowlist", () => {
  const id = "11111111-2222-4333-8444-555555555555";
  const q = (s = "") => new URLSearchParams(s);
  it("maps only known evidence routes and query parameters", () => {
    expect(resolveEvidencePath("GET", ["evidence"], q("search=98&status=trash&evil=1"))).toBe("evidence?search=98&status=trash");
    expect(resolveEvidencePath("GET", ["evidence", id, "image"], q())).toBe(`evidence/${id}/image`);
    expect(resolveEvidencePath("PATCH", ["evidence", id], q())).toBe(`evidence/${id}`);
    expect(resolveEvidencePath("POST", ["evidence", id, "trash"], q())).toBe(`evidence/${id}/trash`);
    expect(resolveEvidencePath("POST", ["evidence", "purge-trash"], q())).toBe("evidence/purge-trash");
  });
  it("refuses traversal, unknown actions and wrong methods", () => {
    expect(resolveEvidencePath("GET", ["evidence", "..", "health"], q())).toBeNull();
    expect(resolveEvidencePath("GET", ["ocr", "extract"], q())).toBeNull();
    expect(resolveEvidencePath("POST", ["evidence", id, "delete"], q())).toBeNull();
    expect(resolveEvidencePath("GET", ["evidence", "purge-trash"], q())).toBeNull();
    expect(resolveEvidencePath("PATCH", ["evidence", "not-a-uuid"], q())).toBeNull();
  });
  it("allows same-origin GETs without Origin but requires Origin for writes", () => {
    expect(checkLocalRequest(headers({ origin: null }), "GET")).toBeNull();
    expect(checkLocalRequest(headers({ origin: null }), "PATCH")?.code).toBe("ORIGIN_NOT_ALLOWED");
    expect(checkLocalRequest(headers({ origin: "https://evil.example" }), "GET")?.code).toBe("ORIGIN_NOT_ALLOWED");
    expect(checkLocalRequest(headers({ "x-gharpayy-client": null }), "GET")?.code).toBe("CLIENT_HEADER_REQUIRED");
  });
});
