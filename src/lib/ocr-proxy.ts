// Used only by the server route handlers. Holds no secrets; LOCAL_API_TOKEN is read in the routes themselves.
import { ALLOWED_IMAGE_TYPES, CLIENT_HEADER, CLIENT_HEADER_VALUE, MAX_UPLOAD_BYTES } from "@/src/lib/ocr-api";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ID_ACTIONS = new Set(["review", "trash", "restore", "retry", "proposals"]);
const QUERY_PARAMS = ["search", "status", "include_trash", "zone_id", "user_id", "date_from", "date_to", "page", "page_size"];
export const JSON_BODY_LIMIT = 1024 * 1024;
export const UPLOAD_METADATA_HEADERS = [
  "x-account-label", "x-filename", "x-source-mode", "x-captured-at", "x-source-label", "x-capture-session-id",
  "x-screenshot-label", "x-original-width", "x-original-height", "x-crop",
];

export type ProxyRejection = { status: number; code: string; message: string };

export function jsonError(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

function hostnameOf(host: string): string | null {
  try {
    return new URL(`http://${host}`).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * Same-origin, loopback-only guard for browser-facing proxy routes (anti-CSRF / DNS rebinding).
 * The custom client header forces a CORS preflight for any cross-site caller, which these routes never
 * approve. State-changing methods additionally require Origin to equal the Host.
 */
export function checkLocalRequest(headers: Headers, method: string): ProxyRejection | null {
  const host = headers.get("host") ?? "";
  const hostname = hostnameOf(host);
  if (!hostname || !LOOPBACK_HOSTS.has(hostname)) return { status: 403, code: "HOST_NOT_ALLOWED", message: "This page must be opened on localhost." };

  const origin = headers.get("origin");
  const safe = method === "GET" || method === "HEAD";
  if (origin !== null || !safe) {
    let originHost: string | null = null;
    try {
      originHost = origin ? new URL(origin).host.toLowerCase() : null;
    } catch {
      originHost = null;
    }
    if (!originHost || originHost !== host.toLowerCase()) return { status: 403, code: "ORIGIN_NOT_ALLOWED", message: "Cross-origin requests are not allowed." };
  }
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite !== null && fetchSite !== "same-origin") return { status: 403, code: "ORIGIN_NOT_ALLOWED", message: "Cross-site requests are not allowed." };
  if (headers.get(CLIENT_HEADER) !== CLIENT_HEADER_VALUE) return { status: 403, code: "CLIENT_HEADER_REQUIRED", message: "Missing client header." };
  return null;
}

/** Guard for the screenshot upload route. */
export function checkProxyRequest(headers: Headers): ProxyRejection | null {
  const base = checkLocalRequest(headers, "POST");
  if (base) return base;
  const type = (headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (!(ALLOWED_IMAGE_TYPES as readonly string[]).includes(type)) return { status: 415, code: "UNSUPPORTED_MEDIA_TYPE", message: "Only PNG, JPEG or WebP screenshots are accepted." };
  const length = headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_UPLOAD_BYTES)) return { status: 413, code: "UPLOAD_TOO_LARGE", message: "The screenshot exceeds the 10 MB limit." };
  return null;
}

/** Map an allowlisted /api/local/evidence/... request onto the FastAPI path. Anything else is refused. */
export function resolveEvidencePath(method: string, segments: string[], search: URLSearchParams): string | null {
  if (segments[0] !== "evidence" || segments.length > 3) return null;
  const [, first, second] = segments;
  let path: string | null = null;
  if (method === "GET") {
    if (first === undefined) path = "evidence";
    else if ((first === "stats" || first === "export") && second === undefined) path = `evidence/${first}`;
    else if (UUID.test(first) && (second === undefined || second === "image")) path = `evidence/${first}${second ? "/image" : ""}`;
  } else if (method === "PATCH") {
    if (first && UUID.test(first) && second === undefined) path = `evidence/${first}`;
  } else if (method === "POST") {
    if (first === "purge-trash" && second === undefined) path = "evidence/purge-trash";
    else if (first && UUID.test(first) && second && ID_ACTIONS.has(second)) path = `evidence/${first}/${second}`;
  }
  if (!path) return null;
  const query = new URLSearchParams();
  for (const key of QUERY_PARAMS) {
    const value = search.get(key);
    if (value !== null) query.set(key, value.slice(0, 200));
  }
  const qs = query.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Read a request body, aborting as soon as it exceeds the byte limit. */
export async function readCappedBody(body: ReadableStream<Uint8Array> | null, limit = MAX_UPLOAD_BYTES): Promise<Uint8Array | null> {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

export function backendBaseUrl() {
  return process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
}

export function backendToken(): string | null {
  const token = process.env.LOCAL_API_TOKEN ?? "";
  return token.length >= 32 ? token : null;
}

/** Forward a request to FastAPI with the server-only token, mapping transport failures to stable errors. */
export async function forwardToBackend(path: string, init: { method: string; headers: Record<string, string>; body?: Uint8Array; timeoutMs: number }) {
  const token = backendToken();
  if (!token) return jsonError(503, "LOCAL_TOKEN_NOT_CONFIGURED", "LOCAL_API_TOKEN is not configured for the local server.");
  let upstream: Response;
  try {
    upstream = await fetch(`${backendBaseUrl()}/api/v1/${path}`, {
      method: init.method,
      body: init.body as BodyInit | undefined,
      headers: { ...init.headers, "x-local-api-token": token },
      cache: "no-store",
      signal: AbortSignal.timeout(init.timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") return jsonError(504, "BACKEND_TIMEOUT", "The local OCR backend did not respond in time.");
    return jsonError(502, "BACKEND_UNAVAILABLE", "The local FastAPI backend is not reachable on port 8000.");
  }
  const type = upstream.headers.get("content-type") ?? "";
  const headers: Record<string, string> = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) headers["set-cookie"] = setCookie;
  if (type.includes("application/json")) {
    headers["content-type"] = "application/json";
    const disposition = upstream.headers.get("content-disposition");
    if (disposition?.startsWith("attachment")) headers["content-disposition"] = disposition;
    return new Response(await upstream.text(), { status: upstream.status, headers });
  }
  const imageType = type.split(";")[0].trim();
  if ((ALLOWED_IMAGE_TYPES as readonly string[]).includes(imageType)) {
    headers["content-type"] = imageType;
    return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers });
  }
  return jsonError(502, "INVALID_BACKEND_RESPONSE", "The local OCR backend returned an unexpected response.");
}
