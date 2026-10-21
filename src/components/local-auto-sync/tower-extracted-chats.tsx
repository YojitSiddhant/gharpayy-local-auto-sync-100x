"use client";
import { useEffect, useRef, useState } from "react";
import { clearMessageAll, getEvidence, saveFieldVerification, type ClearMessageBatchResult, type ClearMessageResult, type EvidenceDetail, type EvidenceSummary } from "@/src/lib/local-evidence";
import { Button } from "@/src/components/ui/button";
import { Sparkles } from "@/src/components/ui/icons";

/** Backend list endpoint cap (backend/app/ocr/evidence.py LIST_LIMIT); older captures are not returned. */
export const LIST_LIMIT = 500;
const DETAIL_CONCURRENCY = 4;
const INDIAN_MOBILE = /^\+91[6-9]\d{9}$/;

type Row = EvidenceDetail["rows"][number];
export type OrderedCapture = EvidenceSummary & { order: number };
export type Occurrence = { capture: OrderedCapture; row: Row };
export type ChatEntry = { kind: "number"; key: string; phone: string; occurrences: Occurrence[] } | { kind: "unconfirmed"; key: string; occurrence: Occurrence };

export const needsCheck = (row: Row) => row.quarantined || row.warnings.length > 0 || row.phone.status !== "candidate";
const approvedValue = (row: Row, field: "phone" | "message") => row.field_verifications?.find((item) => item.field_name === field && item.status === "verified")?.verified_value ?? null;
/** Only the deterministic parser's confident, non-quarantined header reading counts as a contact identity; previews are never read for numbers. */
export const confirmedPhone = (row: Row) => { const verified = approvedValue(row, "phone"); if (verified) return verified; return row.phone.status === "candidate" && !row.quarantined && row.phone.normalized && INDIAN_MOBILE.test(row.phone.normalized) ? row.phone.normalized : null; };
const time = (c: EvidenceSummary) => (c.captured_at ? Date.parse(c.captured_at) : NaN);

/** Oldest first by capture timestamp, ties and missing times broken by stable id. Order numbers start at 1. */
export function orderCaptures(items: EvidenceSummary[]): OrderedCapture[] {
  return [...items].sort((a, b) => { const ta = time(a), tb = time(b); if (Number.isNaN(ta) !== Number.isNaN(tb)) return Number.isNaN(ta) ? 1 : -1; return (ta - tb) || a.id.localeCompare(b.id); }).map((c, i) => ({ ...c, order: i + 1 }));
}

/** Groups confirmed numbers across screenshots (first appearance order); every other reading stays its own entry. */
export function groupExtractedChats(sources: { capture: OrderedCapture; detail: EvidenceDetail }[]): ChatEntry[] {
  const out: ChatEntry[] = [], byPhone = new Map<string, Extract<ChatEntry, { kind: "number" }>>();
  for (const { capture, detail } of [...sources].sort((a, b) => a.capture.order - b.capture.order)) {
    for (const row of [...detail.rows].sort((a, b) => a.position - b.position)) {
      const phone = confirmedPhone(row), occurrence = { capture, row };
      if (!phone) { out.push({ kind: "unconfirmed", key: `${capture.id}:${row.position}`, occurrence }); continue; }
      const existing = byPhone.get(phone);
      if (existing) existing.occurrences.push(occurrence);
      else { const entry = { kind: "number" as const, key: phone, phone, occurrences: [occurrence] }; byPhone.set(phone, entry); out.push(entry); }
    }
  }
  return out;
}

type Loaded = { key: string; detail?: EvidenceDetail; failed?: boolean };
const loadKey = (c: EvidenceSummary) => `${c.id}:${c.status}`;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const stamp = (c: EvidenceSummary) => (c.captured_at ? new Date(c.captured_at).toLocaleString() : "Time unavailable");

/** Consolidated chat list for the screenshots shown in the strip. Reads only those captures' existing detail records. */
export function AllExtractedChats({ captures, capturing, pendingJobs, onOpen }: { captures: OrderedCapture[]; capturing: boolean; pendingJobs: number; onOpen: (id: string) => void }) {
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  const [theme, setTheme] = useState<Theme>("dark");
  const [clearBusy, setClearBusy] = useState(false), [clearResult, setClearResult] = useState<ClearMessageResult | null>(null), [clearBatch, setClearBatch] = useState<ClearMessageBatchResult | null>(null), [clearError, setClearError] = useState("");
  const inflight = useRef(new Set<string>()), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (capturing) return;
    for (const c of captures) {
      const key = loadKey(c);
      if (inflight.current.size >= DETAIL_CONCURRENCY) break;
      if (c.status === "processing" || loaded[c.id]?.key === key || inflight.current.has(key)) continue;
      inflight.current.add(key);
      void getEvidence(c.id).then((detail): Loaded => ({ key, detail }), (): Loaded => ({ key, failed: true })).then((result) => { inflight.current.delete(key); if (alive.current) setLoaded((prev) => ({ ...prev, [c.id]: result })); });
    }
  }, [capturing, captures, loaded]);

  const processing = captures.filter((c) => c.status === "processing").length;
  const ready = captures.flatMap((c) => { const l = loaded[c.id]; return l?.key === loadKey(c) && l.detail ? [{ capture: c, detail: l.detail }] : []; });
  const unavailable = captures.filter((c) => { const l = loaded[c.id]; return l?.key === loadKey(c) && l.failed; });
  const loading = captures.length - processing - ready.length - unavailable.length;
  const ocrFailed = captures.filter((c) => c.status === "error").length;
  const pendingTotal = pendingJobs + processing + loading;
  const entries = groupExtractedChats(ready);
  const retry = () => setLoaded((prev) => Object.fromEntries(Object.entries(prev).filter(([, l]) => !l.failed)));
  async function runClearMessage() {
    if (!ready.length || clearBusy) return;
    setClearBusy(true); setClearError("");
    try { const result = await clearMessageAll("Local operator"); setClearBatch(result); if (result.messages_updated > 0) setLoaded({}); }
    catch (error) { setClearError(error instanceof Error ? error.message : "Clear Message failed."); }
    finally { setClearBusy(false); }
  }


  const t = THEMES[theme];
  return <section aria-labelledby="all-extracted-chats-title" className="min-w-0 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0"><h2 id="all-extracted-chats-title" className="text-lg font-semibold">All Extracted Chats</h2>{!capturing && <p className="text-xs text-muted-foreground">{plural(entries.length, "chat")} from {plural(ready.length, "screenshot")} · unverified OCR observations</p>}</div>
      <div className="flex shrink-0 items-center gap-2"><div role="group" aria-label="Chat list theme" className="inline-flex rounded-full border bg-muted p-0.5 text-xs">{(["light", "dark"] as const).map((value) => <button key={value} type="button" aria-pressed={theme === value} onClick={() => setTheme(value)} className={`rounded-full px-3 py-1 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${theme === value ? "bg-[#00a884] text-white shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>{value === "light" ? "Light Mode" : "Dark Mode"}</button>)}</div><Button size="sm" variant="outline" disabled={clearBusy || !ready.length} onClick={() => void runClearMessage()}><Sparkles className="size-3.5" />{clearBusy ? "Cleaning…" : "Clear Message"}</Button></div>
    </div>
    {clearError && <p role="alert" className="text-xs text-destructive">{clearError}</p>}
    {clearBatch && <ClearPopup result={clearBatch} onClose={() => setClearBatch(null)} />}
    {clearResult && <ClearReview result={clearResult} onOpen={onOpen} onClose={() => setClearResult(null)} onApproved={(detail) => setLoaded((current) => current[clearResult.capture_id] ? { ...current, [clearResult.capture_id]: { ...current[clearResult.capture_id], detail } } : current)} />}
    {capturing ? <p role="status" className="rounded-lg border border-dashed bg-card p-4 text-sm text-muted-foreground">Awaiting Stop — the combined chat list appears after you stop Auto Sync. Screenshots keep appearing above while capturing.</p> : <>
      {pendingTotal > 0 ? <p role="status" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"><span aria-hidden="true" className="mr-1.5 inline-block size-2 animate-pulse rounded-full bg-amber-500" />OCR still pending for {plural(pendingTotal, "screenshot")}. This list is not complete yet and refreshes automatically.</p>
        : captures.length > 0 && unavailable.length === 0 && <p role="status" className="text-xs text-muted-foreground">Includes every screenshot shown above{ocrFailed ? ` · ${plural(ocrFailed, "screenshot")} had no OCR reading` : ""}.</p>}
      {unavailable.length > 0 && <p role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">Could not load extracted data for {plural(unavailable.length, "screenshot")}; this list is incomplete.<Button size="sm" variant="outline" onClick={retry}>Retry</Button></p>}
      {captures.length >= LIST_LIMIT && <p className="text-xs text-muted-foreground">Only the {LIST_LIMIT} most recent screenshots are listed by the server; older captures are not included.</p>}
      <div data-testid="chat-panel" data-theme={theme} className={`flex min-w-0 flex-col overflow-hidden rounded-lg border shadow-sm ${t.panel}`}>
        <div className={`flex items-center justify-between gap-2 px-4 py-2.5 ${t.bar}`}><h3 className="text-sm font-medium">Chats</h3><span className={`rounded-full px-2 py-0.5 text-xs ${t.count}`}>{entries.length}</span></div>
        {captures.length === 0 ? <p className={`px-4 py-6 text-sm ${t.muted}`}>No screenshots captured yet.</p>
          : ready.length > 0 && entries.length === 0 ? <p className={`px-4 py-6 text-sm ${t.muted}`}>No chat rows were read from these screenshots.</p>
          : entries.length === 0 ? <p className={`px-4 py-6 text-sm ${t.muted}`}>Loading extracted chats…</p>
          : <ul aria-label="All extracted chats" className="max-h-[70vh] min-h-0 overflow-y-auto overscroll-contain">{entries.map((entry) => <ChatEntryRow key={entry.key} entry={entry} t={t} onOpen={onOpen} />)}</ul>}
      </div>
    </>}
  </section>;
}

function ClearPopup({ result, onClose }: { result: ClearMessageBatchResult; onClose: () => void }) {
  const changed = result.messages_updated > 0;
  return <div className="fixed right-4 top-4 z-[70] w-[min(92vw,24rem)] rounded-lg border bg-card p-4 shadow-xl" role="status">
    <div className="flex items-start justify-between gap-3"><strong>{changed ? "WhatsApp Business labels cleaned" : "No labels safely identified"}</strong><Button size="sm" variant="outline" onClick={onClose}>Close</Button></div>
    <ul className="mt-2 text-xs"><li>Rows inspected: {result.rows_inspected}</li><li>Labels removed: {result.labels_removed}</li><li>Messages updated: {result.messages_updated}</li><li>Rows unchanged: {result.rows_unchanged}</li><li>Rows requiring manual review: {result.rows_manual_review}</li></ul>
  </div>;
}

function ClearReview({ result, onOpen, onClose, onApproved }: { result: ClearMessageResult; onOpen: (id: string) => void; onClose: () => void; onApproved: (detail: EvidenceDetail) => void }) {
  const [approved, setApproved] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  async function approve(proposal: ClearMessageResult["proposals"][number]) {
    if (saving) return;
    setSaving(true); setError("");
    try {
      const detail = await saveFieldVerification(result.capture_id, { position: proposal.position, field_name: proposal.field_name,
        status: "verified", verified_value: proposal.suggested_value, dataset_split: "unassigned", approved_for_dataset: false, reviewer: "Local operator" });
      onApproved(detail);
      setApproved((current) => ({ ...current, [`${proposal.position}:${proposal.field_name}`]: true }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save approval."); }
    finally { setSaving(false); }
  }
  return <div className="fixed inset-0 z-[60] overflow-y-auto bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="clear-message-title">
    <div className="mx-auto max-w-2xl rounded-lg border bg-card p-4 shadow-xl"><div className="flex items-center justify-between gap-3"><h2 id="clear-message-title" className="font-semibold">Clear Message review</h2><Button size="sm" variant="outline" onClick={onClose}>Close</Button></div>
      <p className="mt-1 text-xs text-muted-foreground">Groq suggestions are unverified proposals. Original OCR remains unchanged.</p>
      {result.proposals.length === 0 ? <p className="py-6 text-sm text-muted-foreground">No safe correction proposals were returned.</p> : <ul className="mt-4 space-y-3">{result.proposals.map((proposal) => { const key = `${proposal.position}:${proposal.field_name}`; return <li key={key} className="rounded-md border p-3 text-sm"><div className="flex flex-wrap items-center justify-between gap-2"><strong>Row {proposal.position} · {proposal.field_name}</strong><Button size="sm" variant="outline" onClick={() => onOpen(result.capture_id)}>View source screenshot</Button></div><p className="mt-2"><span className="text-muted-foreground">Original:</span> {proposal.original_value || "Not captured"}</p><p><span className="text-muted-foreground">Suggested:</span> {proposal.suggested_value}</p><p className="mt-1 text-xs text-amber-700">{proposal.uncertain ? "Uncertain — review carefully before approving." : "Model marked this as clear; human approval is still required."}</p><Button className="mt-2" size="sm" disabled={saving || approved[key]} onClick={() => void approve(proposal)}>{approved[key] ? "Approved" : "Approve correction"}</Button></li>; })}</ul>}
      {error && <p role="alert" className="mt-3 text-xs text-destructive">{error}</p>}
    </div>
  </div>;
}

type Theme = "light" | "dark";
type Tokens = Record<"panel" | "bar" | "count" | "row" | "sep" | "primary" | "muted" | "faint" | "avatar" | "link" | "chip" | "ok" | "warn", string>;
/** WhatsApp Web-inspired palettes, scoped to this panel only (the app theme is untouched). */
const THEMES: Record<Theme, Tokens> = {
  light: { panel: "border-[#e9edef] bg-white text-[#111b21]", bar: "bg-[#f0f2f5]", count: "bg-white text-[#54656f]", row: "hover:bg-[#f5f6f6]", sep: "border-[#e9edef]", primary: "text-[#111b21]", muted: "text-[#667781]", faint: "text-[#667781]/80", avatar: "bg-[#dfe5e7] text-white", link: "text-[#008069] hover:underline", chip: "bg-[#f0f2f5] text-[#54656f]", ok: "bg-[#d9fdd3] text-[#0a5c36]", warn: "bg-[#fff3d6] text-[#7a4b00]" },
  dark: { panel: "border-[#222d34] bg-[#111b21] text-[#e9edef]", bar: "bg-[#202c33]", count: "bg-[#111b21] text-[#8696a0]", row: "hover:bg-[#202c33]", sep: "border-[#222d34]", primary: "text-[#e9edef]", muted: "text-[#8696a0]", faint: "text-[#8696a0]/80", avatar: "bg-[#6a7175] text-[#cfd4d6]", link: "text-[#00a884] hover:underline", chip: "bg-[#202c33] text-[#8696a0]", ok: "bg-[#005c4b] text-[#d9fdd3]", warn: "bg-[#ffd279]/15 text-[#ffd279]" },
};

function Avatar({ t }: { t: Tokens }) { return <span aria-hidden="true" className={`flex size-10 shrink-0 items-center justify-center rounded-full sm:size-12 ${t.avatar}`}><svg viewBox="0 0 24 24" className="size-6 sm:size-7" fill="currentColor"><circle cx="12" cy="8.5" r="4" /><path d="M4 20.5c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5z" /></svg></span>; }
function SourceLink({ capture, t, onOpen }: { capture: OrderedCapture; t: Tokens; onOpen: (id: string) => void }) {
  return <button type="button" onClick={() => onOpen(capture.id)} className={`text-[11px] ${t.link}`} aria-label={`Open screenshot #${capture.order} details`} title={stamp(capture)}>Screenshot #{capture.order}</button>;
}
function Verification({ capture, t }: { capture: OrderedCapture; t: Tokens }) {
  return capture.status === "reviewed" ? <span className={`rounded-full px-1.5 py-0.5 text-[11px] ${t.ok}`}>Screenshot reviewed</span> : null;
}
function ReviewNote({ row, t }: { row: Row; t: Tokens }) {
  if (!needsCheck(row)) return null;
  return <details className="text-[11px]"><summary className={`inline-flex cursor-pointer list-none rounded-full px-1.5 py-0.5 ${t.warn}`}>Needs review{row.warnings.length > 0 && ` · ${plural(row.warnings.length, "warning")}`}</summary><ul className={`mt-1 list-disc space-y-0.5 pl-5 break-words ${t.muted}`}>{row.phone.status !== "candidate" && <li>Phone reading: {row.phone.status}{row.phone.raw ? ` (“${row.phone.raw}”)` : ""}</li>}{row.quarantined && <li>Held for review, not used automatically.</li>}{row.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>;
}
function Time({ row, t }: { row: Row; t: Tokens }) {
  return row.visible_timestamp_text ? <time className={`shrink-0 text-xs ${t.muted}`}>{row.visible_timestamp_text}</time> : <span className={`shrink-0 text-xs italic ${t.faint}`}>No time</span>;
}
function Preview({ row, t }: { row: Row; t: Tokens }) {
  const message = approvedValue(row, "message") ?? row.message_preview;
  const labels = row.business_labels ?? [];
  return <><span className={`block min-w-0 truncate text-sm ${message ? t.muted : `italic ${t.faint}`}`} title={message ?? undefined}>{message || "Message not captured"}</span>{labels.length > 0 && <span className="mt-1 flex flex-wrap gap-1">{labels.map((label) => <span key={label} className={`rounded-full px-1.5 py-0.5 text-[10px] ${t.chip}`}>{label}</span>)}</span>}</>;
}
/** Source, verification and review state for one observation, kept on a quiet third line. */
function Meta({ occurrence: { capture, row }, t, onOpen }: { occurrence: Occurrence; t: Tokens; onOpen: (id: string) => void }) {
  return <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"><SourceLink capture={capture} t={t} onOpen={onOpen} /><Verification capture={capture} t={t} />{row.observed_name && <span className={`text-[11px] ${t.muted}`}>Reviewer-entered name: {row.observed_name}</span>}<ReviewNote row={row} t={t} /></div>;
}
const rowShell = (t: Tokens) => `flex gap-3 px-3 pt-2.5 sm:px-4 ${t.row}`;
const body = (t: Tokens) => `min-w-0 flex-1 space-y-0.5 border-b pb-2.5 ${t.sep}`;

function ChatEntryRow({ entry, t, onOpen }: { entry: ChatEntry; t: Tokens; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  if (entry.kind === "unconfirmed") {
    const { row, capture } = entry.occurrence;
    const label = row.phone.status === "ambiguous" || row.phone.status === "conflict" ? "Number needs review" : "Number not captured";
    return <li className={rowShell(t)}><Avatar t={t} /><div className={body(t)}>
      <button type="button" onClick={() => onOpen(capture.id)} aria-label={`Open ${label.toLowerCase()} in screenshot #${capture.order}`} className="block w-full min-w-0 space-y-0.5 text-left">
        <span className="flex min-w-0 items-baseline justify-between gap-2"><span className="flex min-w-0 items-baseline gap-2"><span className={`shrink-0 text-[15px] italic ${t.muted}`}>{label}</span>{row.display_header_text && <span className={`min-w-0 truncate text-xs ${t.faint}`} title={row.display_header_text}>OCR header: “{row.display_header_text}”</span>}</span><Time row={row} t={t} /></span>
        <Preview row={row} t={t} />
      </button>
      <Meta occurrence={entry.occurrence} t={t} onOpen={onOpen} />
    </div></li>;
  }
  const occ = entry.occurrences, first = occ[0], screenshots = new Set(occ.map((o) => o.capture.id)).size;
  // A single preview is shown only when every observation agrees; otherwise none is promoted as "latest".
  const agree = occ.every((o) => (approvedValue(o.row, "message") ?? o.row.message_preview) === (approvedValue(first.row, "message") ?? first.row.message_preview) && o.row.visible_timestamp_text === first.row.visible_timestamp_text);
  const head = <span className="flex min-w-0 items-baseline justify-between gap-2"><span className={`min-w-0 truncate text-[15px] font-medium ${t.primary}`}>{entry.phone}</span>{agree ? <Time row={first.row} t={t} /> : <span className={`shrink-0 text-xs ${t.muted}`}>{occ.length} readings</span>}</span>;
  if (occ.length === 1) return <li className={rowShell(t)}><Avatar t={t} /><div className={body(t)}>
    <button type="button" onClick={() => onOpen(first.capture.id)} aria-label={`Open ${entry.phone} in screenshot #${first.capture.order}`} className="block w-full min-w-0 space-y-0.5 text-left">{head}<Preview row={first.row} t={t} /></button>
    <Meta occurrence={first} t={t} onOpen={onOpen} />
  </div></li>;
  const reviewCount = occ.filter((o) => needsCheck(o.row)).length;
  return <li className={rowShell(t)}><Avatar t={t} /><div className={body(t)}>
    <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} aria-label={`${entry.phone}, seen in ${screenshots} screenshots`} className="block w-full min-w-0 space-y-0.5 text-left">
      {head}
      {agree ? <Preview row={first.row} t={t} /> : <span className={`block truncate text-sm italic ${t.faint}`}>Visible messages differ across screenshots — expand to compare</span>}
    </button>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]"><button type="button" onClick={() => setOpen((v) => !v)} className={t.link}>{open ? "Hide" : "Show"} {occ.length} observations</button><span className={`rounded-full px-1.5 py-0.5 ${t.chip}`}>Seen in {screenshots} screenshots</span>{reviewCount > 0 && <span className={`rounded-full px-1.5 py-0.5 ${t.warn}`}>{reviewCount} need review</span>}</div>
    {open && <ol aria-label={`Observations of ${entry.phone}`} className={`mt-1.5 space-y-2 border-l-2 pl-3 ${t.sep}`}>{occ.map((o) => <li key={`${o.capture.id}:${o.row.position}`} className="min-w-0 space-y-0.5"><div className="flex min-w-0 items-baseline justify-between gap-2"><Preview row={o.row} t={t} /><Time row={o.row} t={t} /></div><Meta occurrence={o} t={t} onOpen={onOpen} /></li>)}</ol>}
  </div></li>;
}
