import { checkProxyRequest, forwardToBackend, jsonError, readCappedBody, UPLOAD_METADATA_HEADERS } from "@/src/lib/ocr-proxy";

/**
 * Same-origin proxy: browser → Next.js server → FastAPI (loopback).
 * Used by both manual uploads and Share-source frames. The shared LOCAL_API_TOKEN is server-only.
 * Screenshot bytes are forwarded, never logged.
 */
export async function POST(request: Request) {
  const rejection = checkProxyRequest(request.headers);
  if (rejection) return jsonError(rejection.status, rejection.code, rejection.message);

  const body = await readCappedBody(request.body);
  if (body === null) return jsonError(413, "UPLOAD_TOO_LARGE", "The screenshot exceeds the 10 MB limit.");
  if (body.byteLength === 0) return jsonError(400, "EMPTY_UPLOAD", "The uploaded file is empty.");

  const headers: Record<string, string> = { "content-type": request.headers.get("content-type") ?? "" };
  for (const name of UPLOAD_METADATA_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers[name] = value;
  }
  return forwardToBackend("ocr/extract", { method: "POST", headers, body, timeoutMs: 90_000 });
}
