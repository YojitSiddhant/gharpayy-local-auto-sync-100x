import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "gharpayy_session";
const BACKEND = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
const TOKEN = process.env.LOCAL_API_TOKEN ?? "";

async function identity(request: NextRequest): Promise<{ role: string } | null> {
  const cookie = request.cookies.get(SESSION_COOKIE)?.value;
  if (!cookie || TOKEN.length < 32) return null;
  try {
    const response = await fetch(`${BACKEND}/api/v1/auth/me`, {
      headers: { cookie: `${SESSION_COOKIE}=${cookie}`, "x-local-api-token": TOKEN },
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) return null;
    const body = await response.json() as { user?: { role?: string } };
    return body.user?.role ? { role: body.user.role } : null;
  } catch {
    return null;
  }
}

export async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const isAdmin = path === "/admin" || path.startsWith("/admin/");
  const isTower = path === "/tower/auto-sync" || path.startsWith("/tower/auto-sync/");
  const isLogin = path === "/login";
  const isHome = path === "/";
  if (!isHome && !isAdmin && !isTower && !isLogin) return NextResponse.next();

  const user = await identity(request);
  if (!user) {
    if (isHome || isAdmin || isTower) return NextResponse.redirect(new URL("/login", request.url));
    return NextResponse.next();
  }
  const destination = user.role === "SUPER_ADMIN" ? "/admin" : "/tower/auto-sync";
  if (isLogin || isHome) return NextResponse.redirect(new URL(destination, request.url));
  if (isAdmin && user.role !== "SUPER_ADMIN") return NextResponse.redirect(new URL("/tower/auto-sync", request.url));
  return NextResponse.next();
}

export const config = { matcher: ["/", "/login", "/admin/:path*", "/tower/auto-sync/:path*"] };
