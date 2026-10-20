"use client";
import { useEffect, useState } from "react";

type Zone = { id: string; name: string; is_active: boolean; user_count: number };
const headers = { "x-gharpayy-client": "local-auto-sync" };
export default function ZonesPage() {
  const [zones, setZones] = useState<Zone[]>([]); const [name, setName] = useState(""); const [error, setError] = useState("");
  async function load() { const r = await fetch("/api/local/admin/zones", { headers }); if (r.status === 403 || r.status === 401) { setError("Super Admin access is required."); return; } const body = await r.json(); setZones(body.items ?? []); }
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, []);
  async function create() { const r = await fetch("/api/local/admin/zones", { method: "POST", headers: { ...headers, "content-type": "application/json" }, body: JSON.stringify({ name }) }); if (!r.ok) { setError("Unable to create zone."); return; } setName(""); void load(); }
  async function toggle(zone: Zone) { const action = zone.is_active ? "disable" : "enable"; const r = await fetch(`/api/local/admin/zones/${zone.id}/${action}`, { method: "POST", headers }); if (!r.ok) setError("Unable to update zone."); else void load(); }
  return <main className="tower-page"><h1>Zone management</h1><p>Super Admin only. Zones are never deleted.</p><form onSubmit={e => { e.preventDefault(); void create(); }}><input value={name} onChange={e => setName(e.target.value)} placeholder="New zone name" required /><button type="submit">Create zone</button></form>{error && <p role="alert">{error}</p>}<ul>{zones.map(zone => <li key={zone.id}><strong>{zone.name}</strong> — {zone.user_count} users — {zone.is_active ? "Active" : "Disabled"} <button onClick={() => void toggle(zone)}>{zone.is_active ? "Disable" : "Enable"}</button></li>)}</ul><a href="/admin/users">Manage users</a></main>;
}
