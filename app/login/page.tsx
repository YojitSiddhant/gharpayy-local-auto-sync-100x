"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter(); const [username, setUsername] = useState(""); const [password, setPassword] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); const response = await fetch("/api/local/auth/login", { method: "POST", headers: { "content-type": "application/json", "x-gharpayy-client": "local-auto-sync" }, body: JSON.stringify({ username, password }) }); setBusy(false); if (!response.ok) { setError("Username or password is incorrect."); return; } router.push("/"); router.refresh(); }
  return <main style={{ maxWidth: 420, margin: "5rem auto", padding: "2rem" }}><h1>Gharpayy Login</h1><form onSubmit={submit}><label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required /></label><label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>{error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form></main>;
}
