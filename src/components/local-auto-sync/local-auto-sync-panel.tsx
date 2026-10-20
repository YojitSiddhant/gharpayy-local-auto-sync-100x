"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { Check, Download, MonitorUp, Pause, Play, RotateCcw, Save, Search, Square, Trash2, Upload } from "@/src/components/ui/icons";
import { CAPTURE_GAP_MS, CAPTURE_INTERVAL_MS, describeCaptureError, ExactFrameGate, queueErrorAction, startTicker, waitForVideoSize, type Ticker } from "@/src/lib/capture";
import { extractScreenshot, MAX_UPLOAD_BYTES, OcrRequestError, validateScreenshotFile } from "@/src/lib/ocr-api";
import {
  captureMatches, exportEvidence, getEvidence, getEvidenceImageUrl, getStats, listEvidence, markReviewed, proposeRegion,
  purgeTrash, retryOcr, setTrashed, updateEvidence,
  type Bounds, type EvidenceDetail, type EvidenceStats, type EvidenceSummary, type StatusFilter,
} from "@/src/lib/local-evidence";

// Structural port of lead-loved-flow src/components/flow-os/LocalAutoSyncPanel.tsx (route /local-auto-sync).
// Differences by design: OCR runs in local FastAPI + English Tesseract, evidence persists in MySQL
// (instead of Tesseract.js + IndexedDB), and images are served only through the protected local API.

type QueueItem = {
  file: File;
  account: string;
  capturedAt: string;
  sourceMode: "manual-file" | "screen-capture";
  sourceLabel?: string;
  originalDimensions?: { width: number; height: number };
  crop?: Bounds;
};
const emptyBounds: Bounds = { x0: 0, y0: 0, x1: 0, y1: 0 };
type Drafts = { id?: string; text: string | null; label: string | null; names: Record<number, string> };
const noDrafts: Drafts = { text: null, label: null, names: {} };
export const LOCAL_QUEUE_LIMIT = 100;
const SOURCE_UNAVAILABLE = "Shared source is not delivering frames (minimized, hidden or locked). Sampling resumes when it returns.";

function message(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback;
}

export function LocalAutoSyncPanel() {
  const [records, setRecords] = useState<EvidenceSummary[]>([]);
  const [stats, setStats] = useState<EvidenceStats>();
  const [active, setActive] = useState<EvidenceDetail>();
  const [image, setImage] = useState<{ id: string; url: string }>();
  const [selected, setSelected] = useState<string>();
  const [account, setAccount] = useState("Gharpayy WhatsApp");
  const [reviewer, setReviewer] = useState("");
  const [running, setRunning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [source, setSource] = useState<{ width: number; height: number; label: string }>();
  const [crop, setCrop] = useState<Bounds>(emptyBounds);
  const [regionState, setRegionState] = useState<{ id?: string; bounds: Bounds }>({ bounds: emptyBounds });
  const [proposalKind, setProposalKind] = useState<"label" | "unread">("label");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [paused, setPaused] = useState(false);
  const [pending, setPending] = useState(0);
  const [error, setError] = useState("");
  const [skipped, setSkipped] = useState(0);
  const [currentFile, setCurrentFile] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState("");
  const [purgeConfirm, setPurgeConfirm] = useState(false);
  const [now, setNow] = useState(0);
  const [draftState, setDraftState] = useState<Drafts>(noDrafts);
  const queue = useRef<QueueItem[]>([]);
  const processing = useRef(false);
  const pauseRef = useRef(false);
  const stream = useRef<MediaStream | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const timer = useRef<Ticker | null>(null);
  const lastTick = useRef(0);
  const gate = useRef(new ExactFrameGate());
  const sourceUnavailable = useRef(false);
  const captureBusy = useRef(false);
  const startLock = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);
  const input = useRef<HTMLInputElement>(null);
  const query = useRef({ search: "", filter: "all" as StatusFilter });
  const saveTimer = useRef<number | null>(null);
  const drafts = useRef<Drafts>(noDrafts);
  const flushRef = useRef<(keepalive?: boolean) => Promise<boolean>>(async () => true);

  const refresh = useCallback(async () => {
    const [list, counts] = await Promise.all([listEvidence(query.current.search, query.current.filter), getStats()]);
    if (!alive.current) return;
    setRecords(list);
    setStats(counts);
  }, []);

  function stopSampling() {
    timer.current?.stop();
    timer.current = null;
    lastTick.current = 0;
  }

  function stop() {
    generation.current++;
    stopSampling();
    gate.current.reset();
    sourceUnavailable.current = false;
    const old = stream.current;
    stream.current = null;
    old?.getTracks().forEach((t) => t.stop());
    if (video.current) video.current.srcObject = null;
    if (alive.current) {
      setRunning(false);
      setConfirmed(false);
      setSource(undefined);
    }
  }

  useEffect(() => {
    alive.current = true;
    refresh().then(() => alive.current && setReady(true)).catch((e) => alive.current && setError(`Local evidence unavailable: ${message(e, "unknown error")}`));
    const age = window.setInterval(() => setNow(Date.now()), 1000);
    // Persist corrections typed just before reload/close (debounced save may not have fired yet).
    const onPageHide = () => void flushRef.current(true);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      alive.current = false;
      stop();
      window.clearInterval(age);
      window.removeEventListener("pagehide", onPageHide);
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    };
    // Mount/unmount only, like the reference: stop() reads refs and guarded setters, so a stale closure is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  // Search and filter are server-side queries (debounced like typing in the reference search box).
  useEffect(() => {
    query.current = { search, filter };
    if (!ready) return;
    const t = window.setTimeout(() => void refresh().catch((e) => setError(message(e, "Search failed"))), 250);
    return () => window.clearTimeout(t);
  }, [search, filter, ready, refresh]);

  const activeId = records.find((r) => r.id === selected)?.id ?? records[0]?.id;

  // Drafts, region and image are keyed by evidence id, so switching records never shows stale values.
  const draft = draftState.id === activeId ? draftState : noDrafts;
  const region = regionState.id === activeId ? regionState.bounds : emptyBounds;
  const setRegion = (bounds: Bounds) => setRegionState({ id: activeId, bounds });
  const imageUrl = image && activeId && image.id === activeId ? image.url : "";

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    let url = "";
    getEvidence(activeId).then(async (detail) => {
      if (cancelled) return;
      setActive(detail);
      if (detail.evidence_stored) {
        url = await getEvidenceImageUrl(detail.id);
        if (cancelled) URL.revokeObjectURL(url);
        else setImage({ id: detail.id, url });
      }
    }).catch((e) => !cancelled && setError(message(e, "Could not open evidence")));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [activeId]);

  async function drain() {
    if (processing.current || pauseRef.current || !alive.current) return;
    processing.current = true;
    setBusy(true);
    try {
      while (alive.current && !pauseRef.current && queue.current.length) {
        const item = queue.current.shift();
        setPending(queue.current.length);
        if (!item) break;
        setCurrentFile(item.file.name);
        try {
          const result = await extractScreenshot(item.file, item.account, undefined, {
            sourceMode: item.sourceMode, capturedAt: item.capturedAt, sourceLabel: item.sourceLabel,
            originalDimensions: item.originalDimensions, crop: item.crop,
          });
          if (result.duplicate) {
            setSkipped((n) => n + 1);
            if (result.capture.trashed) setError(`${item.file.name}: identical screenshot is already saved but is in Trash. Use the Trash filter to restore it.`);
          }
        } catch (e) {
          const code = e instanceof OcrRequestError ? e.code : "UNKNOWN_ERROR";
          const action = queueErrorAction(code);
          if (action === "pause") {
            // Backpressure: keep the item, pause the queue and stop sampling until the operator resumes.
            queue.current.unshift(item);
            setPending(queue.current.length);
            pauseRef.current = true;
            setPaused(true);
            stop();
            setError(code === "STORAGE_FULL" ? message(e, "Storage full") : `${message(e, "Processing failed")} Remaining queue is paused; press Resume to retry.`);
            break;
          }
          setError(action === "recorded"
            ? `${item.file.name}: ${message(e, "Local OCR failed")} The failed reading was saved; use Retry OCR on it.`
            : `${item.file.name}: ${message(e, "Local OCR failed")} This file was not accepted.`);
        }
        await refresh().catch(() => undefined);
      }
    } finally {
      processing.current = false;
      if (alive.current) {
        setBusy(false);
        setCurrentFile("");
      }
    }
  }

  function enqueue(item: QueueItem) {
    if (queue.current.length + Number(processing.current) >= LOCAL_QUEUE_LIMIT) {
      setError(`Queue full (${LOCAL_QUEUE_LIMIT}); ${item.file.name} was NOT accepted. Add it again when space is available.`);
      stop();
      return;
    }
    if (item.file.size > MAX_UPLOAD_BYTES) {
      setError(`${item.file.name}: NOT accepted; exceeds 10 MB.`);
      return;
    }
    queue.current.push(item);
    setPending(queue.current.length);
    void drain();
  }

  async function addFiles(files: File[]) {
    setError("");
    const problems: string[] = [];
    for (const file of files) {
      const problem = await validateScreenshotFile(file);
      if (problem) problems.push(problem);
      else enqueue({ file, account: account.trim() || "Unlabelled account", capturedAt: new Date().toISOString(), sourceMode: "manual-file" });
    }
    if (problems.length) setError(problems.join(" "));
  }

  function togglePause() {
    pauseRef.current = !pauseRef.current;
    setPaused(pauseRef.current);
    if (!pauseRef.current) {
      setError("");
      void drain();
    }
  }

  async function start() {
    if (startLock.current || stream.current) return;
    startLock.current = true;
    setStarting(true);
    setError("");
    const token = ++generation.current;
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("Screen sharing needs desktop Chrome. Screenshot uploads still work.");
      const shared = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      if (!alive.current || generation.current !== token) {
        shared.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = shared;
      const track = shared.getVideoTracks()[0];
      track?.addEventListener("ended", stop, { once: true });
      const element = video.current;
      if (!element) throw new Error("Source preview unavailable");
      element.srcObject = shared;
      await element.play();
      const sized = await waitForVideoSize(element);
      if (stream.current !== shared || generation.current !== token) return;
      if (!sized) throw new Error("The shared source did not report a frame size. Stop and share it again.");
      const size = { width: element.videoWidth, height: element.videoHeight };
      setSource({ ...size, label: track?.label || "Shared source" });
      setCrop({ x0: 0, y0: 0, x1: size.width, y1: size.height });
      setRunning(true);
      setConfirmed(false);
    } catch (e) {
      stop();
      if (alive.current) setError(describeCaptureError(e));
    } finally {
      startLock.current = false;
      if (alive.current) setStarting(false);
    }
  }

  function retainSource() {
    const shared = stream.current;
    const element = video.current;
    if (!shared || !element || !source || !captureMatches(element.videoWidth, element.videoHeight, source, crop)) {
      setError("Source dimensions or crop mismatch. Reselect source/crop.");
      return;
    }
    stopSampling();
    gate.current.reset();
    sourceUnavailable.current = false;
    const expected = source;
    const selectedCrop = { ...crop };
    const selectedAccount = account.trim() || "Unlabelled account";
    setConfirmed(true);
    const sample = async () => {
      if (stream.current !== shared) return;
      // Reference capture policy: a long gap between ticks (sleep, throttling) requires re-approval.
      const tickAt = Date.now();
      const gap = lastTick.current ? tickAt - lastTick.current : 0;
      lastTick.current = tickAt;
      if (gap > CAPTURE_GAP_MS) {
        stopSampling();
        setConfirmed(false);
        setError(`Capture gap of ${Math.round(gap / 1000)}s detected (computer asleep or tab throttled). Review the crop and confirm again to resume.`);
        return;
      }
      // Backpressure: never sample while paused, processing or with frames still queued.
      if (captureBusy.current || pauseRef.current || processing.current || queue.current.length || element.readyState < 2) return;
      const track = shared.getVideoTracks()[0];
      if (track && (track.muted || track.readyState === "ended")) {
        if (!sourceUnavailable.current) setError(SOURCE_UNAVAILABLE);
        sourceUnavailable.current = true;
        return;
      }
      if (sourceUnavailable.current) {
        sourceUnavailable.current = false;
        setError((current) => (current === SOURCE_UNAVAILABLE ? "" : current));
      }
      if (!captureMatches(element.videoWidth, element.videoHeight, expected, selectedCrop)) {
        // Never resume on a changed source automatically: adopt the new size, reset the crop, require confirmation.
        stopSampling();
        setConfirmed(false);
        const next = { width: element.videoWidth, height: element.videoHeight, label: expected.label };
        if (next.width > 0 && next.height > 0) {
          setSource(next);
          setCrop({ x0: 0, y0: 0, x1: next.width, y1: next.height });
        }
        setError(`Source paused: dimensions/crop changed (now ${next.width} × ${next.height}). Review the crop and confirm again.`);
        return;
      }
      captureBusy.current = true;
      try {
        const canvas = document.createElement("canvas");
        canvas.width = selectedCrop.x1 - selectedCrop.x0;
        canvas.height = selectedCrop.y1 - selectedCrop.y0;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("Capture canvas unavailable");
        ctx.drawImage(element, selectedCrop.x0, selectedCrop.y0, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
        // Exact-pixel gate: identical frames would be exact SHA-256 duplicates; skip them before upload.
        if (gate.current.unchanged(canvas.width, canvas.height, ctx.getImageData(0, 0, canvas.width, canvas.height).data)) {
          setSkipped((n) => n + 1);
          return;
        }
        const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
        if (!blob) throw new Error("Frame encoding failed");
        if (stream.current === shared && alive.current) {
          enqueue({
            file: new File([blob], `capture-${Date.now()}.png`, { type: "image/png" }), account: selectedAccount,
            capturedAt: new Date().toISOString(), sourceMode: "screen-capture", sourceLabel: expected.label,
            originalDimensions: { width: expected.width, height: expected.height }, crop: selectedCrop,
          });
        }
      } catch (e) {
        setError(message(e, "Capture failed"));
        stop();
      } finally {
        captureBusy.current = false;
      }
    };
    timer.current = startTicker(CAPTURE_INTERVAL_MS, () => void sample());
    void sample();
  }

  const applyDetail = useCallback((detail: EvidenceDetail) => {
    setActive(detail);
    setRecords((rs) => rs.map((r) => (r.id === detail.id ? { ...r, label: detail.label, status: detail.status, trashed: detail.trashed } : r)));
  }, []);

  // Persist edits to MySQL shortly after typing stops ("saved as you edit"); failed saves stay in the tab for Save / retry.
  const flush = useCallback(async (keepalive = false) => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = null;
    const pendingDrafts = drafts.current;
    const id = pendingDrafts.id;
    const names = Object.entries(pendingDrafts.names).map(([position, name]) => ({ position: Number(position), name }));
    if (!id || (pendingDrafts.text === null && pendingDrafts.label === null && !names.length)) return true;
    try {
      const detail = await updateEvidence(id, {
        reviewer, ...(pendingDrafts.text !== null ? { text: pendingDrafts.text } : {}),
        ...(pendingDrafts.label !== null ? { screenshot_label: pendingDrafts.label } : {}),
        ...(names.length ? { observed_names: names } : {}),
      }, keepalive);
      if (drafts.current === pendingDrafts) {
        drafts.current = noDrafts;
        setDraftState(noDrafts);
      }
      applyDetail(detail);
      void getStats().then(setStats).catch(() => undefined);
      return true;
    } catch (e) {
      setError(e instanceof OcrRequestError && e.code !== "NETWORK_ERROR" ? e.message : "Save failed; unsaved changes retained in this tab. Use Save to retry.");
      return false;
    }
  }, [reviewer, applyDetail]);

  useEffect(() => {
    flushRef.current = flush;
  }, [flush]);

  function scheduleSave(change: Partial<{ text: string; label: string; name: [number, string] }>) {
    const base = drafts.current.id === activeId ? drafts.current : { ...noDrafts, id: activeId };
    const next: Drafts = { ...base, names: { ...base.names } };
    if (change.text !== undefined) next.text = change.text;
    if (change.label !== undefined) next.label = change.label;
    if (change.name) next.names[change.name[0]] = change.name[1];
    drafts.current = next;
    setDraftState(next);
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void flush(), 800);
  }

  async function act(run: () => Promise<EvidenceDetail | void>, refreshList = true) {
    setSaving(true);
    setError("");
    try {
      if (!(await flush())) return;
      const detail = await run();
      if (detail) applyDetail(detail);
      if (refreshList) await refresh();
    } catch (e) {
      setError(message(e, "Request failed"));
    } finally {
      setSaving(false);
    }
  }

  async function download() {
    try {
      const blob = await exportEvidence();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "gharpayy-local-review.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(message(e, "Export failed"));
    }
  }

  function boundsInputs(value: Bounds, change: (b: Bounds) => void, disabled = false, label = "Region") {
    return (
      <div className="grid grid-cols-4 gap-1">
        {(["x0", "y0", "x1", "y1"] as const).map((key) => (
          <label key={key} className="min-w-0 text-xs">{key}
            <Input aria-label={`${label} ${key}`} type="number" min={0} disabled={disabled} value={value[key]} onChange={(e) => change({ ...value, [key]: Math.floor(Number(e.target.value)) })} className="h-8 min-w-0 w-full" />
          </label>
        ))}
      </div>
    );
  }

  const shown = active && active.id === activeId ? active : undefined;
  const trashed = Boolean(shown?.trash);
  const phones = shown?.rows.filter((r) => r.phone.status === "candidate") ?? [];
  const otherRows = shown?.rows.filter((r) => r.phone.status !== "candidate") ?? [];
  const capturedAt = shown?.captured_at ?? shown?.received_at ?? null;
  const original = shown?.original_dimensions;

  return (
    <section className="min-w-0 space-y-3 p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="text-sm font-semibold">Gharpayy Local Auto Sync · Evidence review</h2><span className="text-xs text-muted-foreground">On-device · Unverified identity · CRM import disabled</span></div>
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant="outline" disabled={!ready} onClick={() => input.current?.click()}><Upload className="size-4" />Screenshots</Button>
          <Button size="sm" disabled={!ready || starting} onClick={() => (running ? stop() : void start())}><MonitorUp className="size-4" />{starting ? "Opening…" : running ? "Stop sharing" : "Share source"}</Button>
          <Button size="icon" variant="outline" disabled={!busy && !pending && !paused} aria-label={paused ? "Resume queue" : "Pause queue"} title={paused ? "Resume queue" : "Pause after current screenshot"} onClick={togglePause}>{paused ? <Play className="size-4" /> : <Pause className="size-4" />}</Button>
          <Button size="icon" variant="ghost" aria-label="Stop capture" title="Stop capture" onClick={stop}><Square className="size-4" /></Button>
        </div>
      </header>
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden data-testid="screenshot-input" onChange={(e) => { void addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Input aria-label="WhatsApp account" value={account} maxLength={90} disabled={running || starting || busy || pending > 0} onChange={(e) => setAccount(e.target.value)} className="h-8 max-w-52" />
        <Input aria-label="Local reviewer" placeholder="Local reviewer (unverified)" value={reviewer} maxLength={120} onChange={(e) => setReviewer(e.target.value)} className="h-8 max-w-52" />
        <span role="status">{!ready ? "Opening local evidence…" : paused ? "Queue paused" : busy ? "Reading locally…" : confirmed ? "Sampling every 5s" : running ? "Preview only" : "Ready"} · {pending} queued · {stats?.records ?? 0} saved · {skipped} same-account duplicates skipped</span>
      </div>
      <p className="text-xs text-muted-foreground">An account label or confirmation cannot prove business ownership. Manual files are unverified inputs. Reviewed ≠ verified identity.</p>
      <div className={running ? "space-y-2 border p-2" : "hidden"}>
        <video ref={video} muted playsInline className="max-h-72 w-full object-contain" aria-label="Shared source preview" />
        {source && <><p className="break-words text-xs">{source.label} · {source.width} × {source.height} · Crop pixels</p>{boundsInputs(crop, setCrop, confirmed, "Crop")}<Button size="sm" disabled={confirmed} onClick={retainSource}>Confirm crop & retain frames</Button></>}
      </div>
      {currentFile && <p className="truncate text-xs text-muted-foreground">{currentFile}</p>}
      {error && <p role="alert" className="break-words text-xs text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-y py-1 text-xs">
        <span>{stats?.review ?? 0} awaiting review</span><span>{stats?.reviewed ?? 0} reviewed, identity unverified</span><span>{stats?.failed ?? 0} failed</span><span>{((stats?.average_ms ?? 0) / 1000).toFixed(1)}s average</span>
        <span className="text-muted-foreground">{((stats?.stored_bytes ?? 0) / 1024 / 1024).toFixed(1)} / {((stats?.max_bytes ?? 0) / 1024 / 1024).toFixed(0)} MB · {stats?.records ?? 0} / {stats?.max_records ?? 0} incl. trash · {stats?.duplicates ?? 0} duplicate uploads</span>
        <Button size="icon" variant="ghost" title="Export active evidence (trash excluded)" aria-label="Export readings" disabled={!stats || stats.records - stats.trash <= 0} onClick={() => void download()}><Download className="size-4" /></Button>
        <Button size="sm" variant="outline" disabled={busy || running || saving || !stats?.trash} onClick={() => setPurgeConfirm(true)}>Purge trash…</Button>
      </div>
      {purgeConfirm && (
        <div className="flex flex-wrap items-center gap-2 border border-destructive p-2 text-xs">
          <span>Permanently delete only trashed evidence. Cannot be undone.</span>
          <Button size="sm" variant="destructive" onClick={() => void act(async () => { await purgeTrash(reviewer); setPurgeConfirm(false); })}>Confirm permanent purge</Button>
          <Button size="sm" variant="outline" onClick={() => setPurgeConfirm(false)}>Cancel</Button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1"><Search className="absolute left-2 top-2 size-4 text-muted-foreground" /><Input aria-label="Search local readings" placeholder="Search number, message or file" value={search} maxLength={200} onChange={(e) => setSearch(e.target.value)} className="h-8 pl-8" /></div>
        <select aria-label="Review status" className="h-8 max-w-full rounded border bg-card px-2 text-xs" value={filter} onChange={(e) => setFilter(e.target.value as StatusFilter)}><option value="all">Active readings</option><option value="review">Needs review</option><option value="reviewed">Reviewed</option><option value="error">Failed</option><option value="trash">Trash</option></select>
      </div>
      {activeId ? <>
        <select aria-label="Captured screenshot" className="h-8 w-full min-w-0 rounded border bg-card px-2 text-xs" value={activeId} onChange={(e) => { void flush(); setSelected(e.target.value); }}>{records.map((r) => <option key={r.id} value={r.id}>{r.label} · {r.status}{r.trashed ? " · trash" : ""}</option>)}</select>
        {shown ? <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,40fr)_minmax(0,60fr)]">
          <div className="min-w-0 space-y-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- protected same-origin object URL, never a public file */}
            <img src={imageUrl || undefined} alt="Local screenshot evidence" className="max-h-[70vh] w-full border object-contain" />
            <Input aria-label="Screenshot label" value={draft.label ?? shown.screenshot_label ?? shown.file_name ?? ""} maxLength={255} onChange={(e) => scheduleSave({ label: e.target.value })} />
            <p className="break-words text-xs text-muted-foreground">{shown.account_label} (unverified) · {shown.source_mode} · File: {shown.file_name ?? "Unknown"} · Captured/imported {capturedAt ? new Date(capturedAt).toLocaleString() : "unknown"} · {now && capturedAt ? Math.max(0, Math.floor((now - Date.parse(capturedAt)) / 1000)) : "…"}s observation age (not response SLA)</p>
            <p className="break-all text-xs text-muted-foreground">Source SHA-256: {shown.sha256 || "Unavailable"} · Original {original?.width ? `${original.width} × ${original.height}` : "dimensions unknown"} · OCR {shown.dimensions.width} × {shown.dimensions.height}{shown.crop ? ` · Crop ${shown.crop.x0}, ${shown.crop.y0}, ${shown.crop.x1}, ${shown.crop.y1}` : ""}{shown.evidence_stored ? "" : " · Image not retained"}</p>
            {shown.cross_account_reuse && <p className="text-xs text-destructive">Identical source reused across account labels — held separately, identity unverified.</p>}
            <div className="flex flex-wrap gap-2">
              <Input aria-label="Trash or restore reason" placeholder="Trash / restore reason" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} className="min-w-0 flex-1" />
              <Button size="sm" variant="outline" disabled={!reason.trim() || saving} onClick={() => void act(async () => { const d = await setTrashed(shown.id, reason, reviewer, trashed); setReason(""); return d; })}>{trashed ? <RotateCcw className="size-3" /> : <Trash2 className="size-3" />}{trashed ? "Restore" : "Trash"}</Button>
            </div>
            {shown.trash && <p className="break-words text-xs">Trashed: {shown.trash.reason}</p>}
            <details className="text-xs"><summary>Trash history ({shown.trash_history.length})</summary>{shown.trash_history.map((h, i) => <p key={i}>{h.time} · {h.action} · {h.reason} · {h.reviewer}</p>)}</details>
          </div>
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-1 text-xs"><span>OCR engine confidence {(shown.result?.mean_confidence ?? 0).toFixed(0)}% ≠ accuracy · {((shown.result?.duration_ms ?? 0) / 1000).toFixed(1)}s</span>
              <div className="flex flex-wrap gap-1">
                <Button size="sm" variant="outline" disabled={saving} onClick={() => void act(async () => undefined)}><Save className="size-3" />Save / retry</Button>
                <Button size="sm" variant="outline" disabled={saving || shown.status !== "review" || trashed} onClick={() => void act(() => markReviewed(shown.id, reviewer))}><Check className="size-3" />Mark reviewed</Button>
                {shown.status === "error" && <Button size="sm" variant="outline" disabled={busy || pending > 0 || trashed || saving || !shown.evidence_stored} onClick={() => void act(() => retryOcr(shown.id, reviewer))}><RotateCcw className="size-3" />Retry OCR</Button>}
              </div>
            </div>
            {shown.error?.message && <p className="break-words text-xs text-destructive">{shown.error.message}</p>}
            {shown.conflicts.length > 0 && <div role="alert" className="space-y-1 border border-destructive p-2 text-xs"><strong>Association quarantined</strong>{shown.conflicts.map((c, i) => <p key={i} className="break-words">{c}</p>)}</div>}
            <label className="block text-xs">Corrected text · saved locally as you edit<textarea aria-label="Review OCR text" disabled={trashed || shown.status === "error" || shown.status === "processing"} className="mt-1 min-h-48 w-full resize-y rounded border bg-card p-2 text-sm leading-relaxed" value={draft.text ?? shown.text} onChange={(e) => scheduleSave({ text: e.target.value })} /></label>
            <details className="text-xs"><summary>Original OCR & correction history ({shown.corrections.length})</summary><pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words border p-2">{shown.original_text}</pre>{shown.corrections.map((h, i) => <div key={i} className="border-b py-2"><p>{h.time} · {h.reviewer} (self-described)</p><p className="whitespace-pre-wrap break-words">Old: {h.old_text}</p><p className="whitespace-pre-wrap break-words">New: {h.new_text}</p></div>)}</details>
            <h3 className="text-xs font-semibold">{phones.length} original header candidates · Unverified</h3>
            <div className="max-h-64 overflow-y-auto divide-y border">
              {phones.map((r) => <div key={r.position} className="space-y-1 p-2 text-xs"><div className="flex flex-wrap justify-between gap-2"><strong>{r.phone.normalized}</strong><span className="text-muted-foreground">{r.visible_timestamp_text || "Time not captured"} · Sender unknown</span></div><p className="break-words leading-relaxed">{r.message_preview || "Preview not captured"}{r.preview_truncated && " (truncated)"}</p><p>{r.header_bounds ? `Header bounds: ${r.header_bounds.left}, ${r.header_bounds.top}, ${r.header_bounds.right}, ${r.header_bounds.bottom}` : "Header coordinates unavailable"}</p><Input aria-label={`Candidate name ${r.position}`} placeholder="Observed name (review proposal only)" value={draft.names[r.position] ?? r.observed_name ?? ""} maxLength={255} disabled={trashed} onChange={(e) => scheduleSave({ name: [r.position, e.target.value] })} /></div>)}
            </div>
            {otherRows.length > 0 && <details className="text-xs"><summary>{otherRows.length} other parsed rows · no verified phone header</summary><div className="divide-y border">{otherRows.map((r) => <div key={r.position} className="space-y-1 p-2"><div className="flex flex-wrap justify-between gap-2"><strong className="break-all">{r.display_header_text ?? "Header not captured"}</strong><span className="text-muted-foreground">{r.visible_timestamp_text || "Time not captured"} · Sender unknown</span></div><p className="break-words">{r.message_preview || "Preview not captured"}{r.preview_truncated && " (truncated)"}</p>{r.warnings.length > 0 && <p className="break-words text-muted-foreground">{r.warnings.join(" · ")}</p>}</div>)}</div></details>}
            <div className="flex flex-wrap gap-1">{shown.signals.map((s, i) => <span key={i} className="max-w-full break-words rounded border px-2 py-1 text-xs">Tentative {s.kind}: “{s.evidence}”</span>)}</div>
            <p className="text-xs text-muted-foreground">Content cues are matched words, not true emotions or automatic rejection. No inferred message counts or chat completeness.</p>
            <details className="text-xs"><summary>Image-region label / unread proposals · {shown.result?.line_count ?? 0} OCR lines</summary><div className="space-y-2 py-2"><p>Operator-selected region in OCR image pixels; all results tentative, color meaning unknown.</p><select aria-label="Region field kind" value={proposalKind} onChange={(e) => setProposalKind(e.target.value === "unread" ? "unread" : "label")} className="rounded border bg-card p-1"><option value="label">Label chip region</option><option value="unread">Unread badge region</option></select>{boundsInputs(region, setRegion)}<Button size="sm" variant="outline" disabled={!shown.result?.lines.length || trashed || saving} onClick={() => { if (!captureMatches(shown.dimensions.width, shown.dimensions.height, shown.dimensions, region)) { setError("Select a valid image region in OCR image coordinates."); return; } void act(() => proposeRegion(shown.id, proposalKind, region), false); }}>Propose from region</Button>{shown.proposals.map((p, i) => <p key={i} className="break-words rounded border p-2">{p.kind === "label" ? "Label proposal" : "Unread badge proposal"}: {p.text} · bounds {p.bounds.x0}, {p.bounds.y0}, {p.bounds.x1}, {p.bounds.y1} · sampled color {p.color ?? "unknown"} (meaning unknown) · count {p.count ?? "unknown"} · tentative</p>)}<details><summary>Retained line / word coordinates</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(shown.result?.lines ?? [], null, 2)}</pre></details></div></details>
          </div>
        </div> : <p className="py-6 text-center text-sm text-muted-foreground">Opening evidence…</p>}
      </> : <p className="py-6 text-center text-sm text-muted-foreground">{ready && (search || filter !== "all") ? "No matching readings" : "No local screenshots yet"}</p>}
      <aside className="space-y-1 border-t pt-2 text-xs" aria-label="Release target"><strong>99.9% release target — NOT VALIDATED</strong><p>Automated test count ≠ OCR accuracy. Independently labeled dataset: not available.</p><p>Phone exact-match precision / recall: not measured · Accepted coverage: not measured · Label text / bounds / color metrics: not measured · Unread count / bounds metrics: not measured.</p><p className="text-muted-foreground">English OCR · Local FastAPI + MySQL evidence · No hosted inference · Review only, CRM import disabled · 1,800/day sustained capacity unverified · Queue/files in memory do not survive tab closure.</p></aside>
    </section>
  );
}
