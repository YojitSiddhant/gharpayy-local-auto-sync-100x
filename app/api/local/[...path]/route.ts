import { checkLocalRequest, forwardToBackend, JSON_BODY_LIMIT, jsonError, readCappedBody, resolveEvidencePath } from "@/src/lib/ocr-proxy";

type Context = { params: Promise<{ path: string[] }> };

/**
 * Same-origin proxy for the protected saved-evidence API. Only allowlisted paths and query
 * parameters are forwarded; the server-only token is added here and never reaches the browser.
 */
async function handle(request: Request, context: Context) {
  const method = request.method.toUpperCase();
  const rejection = checkLocalRequest(request.headers, method);
  if (rejection) return jsonError(rejection.status, rejection.code, rejection.message);

  const { path } = await context.params;
  const target = resolveEvidencePath(method, path, new URL(request.url).searchParams);
  if (!target) return jsonError(404, "NOT_FOUND", "Unknown local API path.");

  if (method === "GET") return forwardToBackend(target, { method, headers: { cookie: request.headers.get("cookie") ?? "" }, timeoutMs: 30_000 });

  const type = (request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") return jsonError(415, "UNSUPPORTED_MEDIA_TYPE", "Expected a JSON request body.");
  const body = await readCappedBody(request.body, JSON_BODY_LIMIT);
  if (body === null) return jsonError(413, "BODY_TOO_LARGE", "Request body is too large.");
  return forwardToBackend(target, {
    method,
    headers: { "content-type": "application/json", cookie: request.headers.get("cookie") ?? "" },
    body: body.byteLength ? body : new TextEncoder().encode("{}"),
    timeoutMs: target.endsWith("/retry") ? 90_000 : 30_000,
  });
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
