import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalAutoSyncPanel } from "@/src/components/local-auto-sync/local-auto-sync-panel";
import type { EvidenceDetail, EvidenceSummary } from "@/src/lib/local-evidence";
import { detail as baseDetail, ID, jsonResponse, pngFile, sampleResponse, stats as baseStats, summary } from "@/src/test-fixtures/ocr";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

/** In-memory fake of the same-origin local API (Next.js proxy → FastAPI). */
function fakeBackend(options: { records?: EvidenceSummary[]; extract?: () => Promise<Response> } = {}) {
  const calls: Call[] = [];
  let records = options.records ?? [summary];
  let current: EvidenceDetail = { ...baseDetail };
  const fetchMock = vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    calls.push({ url, method, headers: (init.headers ?? {}) as Record<string, string>, body });
    if (url.startsWith("/api/ocr/extract")) {
      if (options.extract) return options.extract();
      records = [summary];
      return jsonResponse(sampleResponse, 201);
    }
    const path = url.replace("/api/local/evidence", "");
    if (path.startsWith("?")) return jsonResponse({ items: records });
    if (path === "/stats") return jsonResponse({ ...baseStats, records: records.length, trash: records.filter((r) => r.trashed).length });
    if (path === "/export") return jsonResponse([{ id: ID }]);
    if (path === "/purge-trash") { records = records.filter((r) => !r.trashed); return jsonResponse({ purged: 1 }); }
    if (path === `/${ID}/image`) return jsonResponse({});
    if (path === `/${ID}` && method === "PATCH") {
      const patch = body as { text?: string; reviewer?: string };
      if (patch.text !== undefined) current = { ...current, text: patch.text, corrections: [...current.corrections, { time: "t", old_text: current.text, new_text: patch.text, reviewer: patch.reviewer || "Unidentified local reviewer" }] };
      return jsonResponse(current);
    }
    if (path === `/${ID}`) return jsonResponse(current);
    if (path === `/${ID}/review`) { current = { ...current, status: "reviewed" }; return jsonResponse(current); }
    if (path === `/${ID}/trash`) { current = { ...current, trashed: true, trash: { time: "t", reason: (body as { reason: string }).reason, reviewer: "R" }, trash_history: [{ action: "trash", time: "t", reason: "r", reviewer: "R" }] }; return jsonResponse(current); }
    return jsonResponse({ error: { code: "NOT_FOUND", message: "nope" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const choose = (files: File[]) => fireEvent.change(screen.getByTestId("screenshot-input"), { target: { files } });

/**
 * The empty state is rendered before the initial evidence load finishes, while Screenshots / Share source are
 * still disabled (`!ready`). Wait for loading to complete before interacting, otherwise clicks can land on a
 * disabled button on slower machines (the cause of the GitHub Actions failure).
 */
async function waitUntilReady() {
  await screen.findByText("No local screenshots yet");
  await waitFor(() => expect(screen.getByRole("button", { name: /Share source/ })).toBeEnabled());
}

describe("LocalAutoSyncPanel — reference layout", () => {
  it("renders the reference header, controls, stats, filters and footer", async () => {
    fakeBackend({ records: [] });
    render(<LocalAutoSyncPanel />);
    expect(screen.getByRole("heading", { name: "Gharpayy Local Auto Sync · Evidence review" })).toBeInTheDocument();
    expect(screen.getByText("On-device · Unverified identity · CRM import disabled")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Screenshots/ })).toBeEnabled());
    expect(screen.getByRole("button", { name: /Share source/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause queue" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop capture" })).toBeInTheDocument();
    expect(screen.getByLabelText("WhatsApp account")).toHaveValue("Gharpayy WhatsApp");
    expect(screen.getByPlaceholderText("Local reviewer (unverified)")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Ready · 0 queued · 0 saved · 0 same-account duplicates skipped");
    expect(screen.getByText(/0 reviewed, identity unverified/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search number, message or file")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Review status")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Active readings", "Needs review", "Reviewed", "Failed", "Trash"]);
    expect(screen.getByText("No local screenshots yet")).toBeInTheDocument();
    expect(screen.getByText("99.9% release target — NOT VALIDATED")).toBeInTheDocument();
  });

  it("restores saved evidence from the backend on load (reload persistence)", async () => {
    fakeBackend();
    render(<LocalAutoSyncPanel />);
    expect(await screen.findByLabelText("Captured screenshot")).toHaveDisplayValue("synthetic-chat.png · review");
    expect(await screen.findByAltText("Local screenshot evidence")).toHaveAttribute("src", "blob:preview");
    expect(screen.getByText(/Source SHA-256: a{64}/)).toBeInTheDocument();
    expect(screen.getByText(/OCR engine confidence 91% ≠ accuracy · 1.2s/)).toBeInTheDocument();
    expect(screen.getByText("1 original header candidates · Unverified")).toBeInTheDocument();
    expect(screen.getByText("+919876543210")).toBeInTheDocument();
    expect(screen.getByText("2:52 pm · Sender unknown")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Association quarantined");
    expect(screen.getByText("Tentative urgency: “urgent”")).toBeInTheDocument();
    expect(screen.getByLabelText("Review OCR text")).toHaveValue(baseDetail.text);
  });
});

describe("LocalAutoSyncPanel — uploads and queue", () => {
  it("rejects invalid files without calling the backend", async () => {
    const { calls } = fakeBackend({ records: [] });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([new File(["hello"], "notes.txt", { type: "text/plain" })]);
    expect(await screen.findByRole("alert")).toHaveTextContent("notes.txt: only PNG, JPEG or WebP");
    expect(calls.some((c) => c.url.startsWith("/api/ocr"))).toBe(false);
  });

  it("uploads through the shared OCR path with manual-file metadata and shows the result", async () => {
    const { calls } = fakeBackend({ records: [] });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile()]);
    expect(await screen.findByLabelText("Captured screenshot")).toBeInTheDocument();
    const upload = calls.find((c) => c.url === "/api/ocr/extract")!;
    expect(upload.headers["x-source-mode"]).toBe("manual-file");
    expect(upload.headers["x-account-label"]).toBe("Gharpayy%20WhatsApp");
  });

  it("counts same-account duplicates as skipped", async () => {
    fakeBackend({ extract: async () => jsonResponse({ ...sampleResponse, duplicate: true }) });
    render(<LocalAutoSyncPanel />);
    await screen.findByLabelText("Captured screenshot");
    choose([pngFile()]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("1 same-account duplicates skipped"));
  });

  it("pauses and resumes the queue", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const { calls } = fakeBackend({ records: [], extract: async () => { await gate; return jsonResponse(sampleResponse, 201); } });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile("one.png"), pngFile("two.png")]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Reading locally…"));
    fireEvent.click(screen.getByRole("button", { name: "Pause queue" }));
    await act(async () => release());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Queue paused · 1 queued"));
    expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Resume queue" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(2));
  });

  it("pauses the queue on storage full and shows OCR errors", async () => {
    fakeBackend({ records: [], extract: async () => jsonResponse({ error: { code: "STORAGE_FULL", message: "Storage full. Trash does not free storage until explicitly purged. Remaining queue is paused." } }, 507) });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile()]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Storage full");
    expect(screen.getByRole("button", { name: "Resume queue" })).toBeEnabled();
  });
});

describe("LocalAutoSyncPanel — review workflow", () => {
  it("autosaves corrections with the reviewer and keeps the original", async () => {
    const { calls } = fakeBackend();
    render(<LocalAutoSyncPanel />);
    const text = await screen.findByLabelText("Review OCR text");
    fireEvent.change(screen.getByLabelText("Local reviewer"), { target: { value: "Asha" } });
    fireEvent.change(text, { target: { value: "corrected text" } });
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true), { timeout: 2000 });
    expect(calls.find((c) => c.method === "PATCH")!.body).toMatchObject({ text: "corrected text", reviewer: "Asha" });
    await waitFor(() => expect(screen.getByText("Original OCR & correction history (1)")).toBeInTheDocument());
  });

  it("marks reviewed and requires a reason to trash", async () => {
    const { calls } = fakeBackend();
    render(<LocalAutoSyncPanel />);
    await screen.findByLabelText("Review OCR text");
    fireEvent.click(screen.getByRole("button", { name: /Mark reviewed/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/review"))).toBe(true));
    const trash = screen.getByRole("button", { name: /^Trash$/ });
    expect(trash).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Trash or restore reason"), { target: { value: "Wrong input" } });
    fireEvent.click(trash);
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/trash"))?.body).toMatchObject({ reason: "Wrong input" }));
  });

  it("searches and filters through the backend", async () => {
    const { calls } = fakeBackend();
    render(<LocalAutoSyncPanel />);
    await screen.findByLabelText("Captured screenshot");
    fireEvent.change(screen.getByLabelText("Search local readings"), { target: { value: "98765" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("search=98765"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Review status"), { target: { value: "trash" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=trash"))).toBe(true));
  });

  it("exports JSON and purges only after explicit confirmation", async () => {
    const { calls } = fakeBackend({ records: [summary, { ...summary, id: "22222222-2222-4333-8444-555555555555", trashed: true }] });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<LocalAutoSyncPanel />);
    await screen.findByLabelText("Captured screenshot");
    fireEvent.click(screen.getByRole("button", { name: "Export readings" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith("/export"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Purge trash…" }));
    expect(calls.some((c) => c.url.endsWith("/purge-trash"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm permanent purge" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/purge-trash"))?.body).toMatchObject({ confirm: true }));
  });
});

describe("LocalAutoSyncPanel — Share source capture", () => {
  function fakeScreen(width = 1440, height = 900) {
    const track = { stop: vi.fn(), label: "screen:0", addEventListener: vi.fn(), muted: false, readyState: "live" };
    const stream = { getTracks: () => [track], getVideoTracks: () => [track] } as unknown as MediaStream;
    const getDisplayMedia = vi.fn(async () => stream);
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getDisplayMedia } });
    const size = { width, height };
    Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", { configurable: true, get: () => size.width });
    Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", { configurable: true, get: () => size.height });
    Object.defineProperty(HTMLMediaElement.prototype, "readyState", { configurable: true, get: () => 4 });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    // Each drawn frame differs by one pixel unless `frames.same` is set (exact-pixel gate input).
    const frames = { n: 0, same: false };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
      getImageData: (_x: number, _y: number, w: number, h: number) => {
        const data = new Uint8ClampedArray(w * h * 4);
        data[0] = frames.same ? 7 : ++frames.n % 255;
        return { data, width: w, height: h };
      },
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (cb: BlobCallback) { cb(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" })); });
    return { track, getDisplayMedia, size, frames };
  }

  it("requires a user click, shows preview and crop, samples frames and stops sharing", async () => {
    const { calls } = fakeBackend({ records: [] });
    const { track, getDisplayMedia } = fakeScreen();
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    expect(getDisplayMedia).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Share source/ }));
    expect(await screen.findByText("screen:0 · 1440 × 900 · Crop pixels")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Preview only");
    expect(calls.some((c) => c.url === "/api/ocr/extract")).toBe(false);
    fireEvent.change(screen.getByLabelText("Crop x1"), { target: { value: "720" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm crop & retain frames" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/ocr/extract")).toBe(true));
    const frame = calls.find((c) => c.url === "/api/ocr/extract")!;
    expect(frame.headers).toMatchObject({ "x-source-mode": "screen-capture", "x-crop": "0,0,720,900", "x-original-width": "1440", "x-source-label": "screen%3A0" });
    fireEvent.click(screen.getByRole("button", { name: /Stop sharing/ }));
    expect(track.stop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Share source/ })).toBeInTheDocument();
  });

  it("pauses sampling when the source dimensions change", async () => {
    fakeBackend({ records: [] });
    const { size } = fakeScreen();
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByRole("button", { name: /Share source/ }));
    await screen.findByText(/Crop pixels/);
    fireEvent.click(screen.getByRole("button", { name: "Confirm crop & retain frames" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Sampling every 5s · 0 queued · 1 saved"));
    size.width = 1280;
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(await screen.findByText(/Source paused: dimensions\/crop changed/)).toHaveAttribute("role", "alert");
  });

  it("explains when screen sharing is unavailable", async () => {
    fakeBackend({ records: [] });
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    fireEvent.click(screen.getByRole("button", { name: /Share source/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Screen sharing needs desktop Chrome");
  });

  async function confirmedCapture(calls: Call[]) {
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    fireEvent.click(screen.getByRole("button", { name: /Share source/ }));
    await screen.findByText(/Crop pixels/);
    fireEvent.click(screen.getByRole("button", { name: "Confirm crop & retain frames" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Sampling every 5s · 0 queued"));
  }

  it("skips byte-identical frames before upload (exact-pixel gate)", async () => {
    const { calls } = fakeBackend({ records: [] });
    const { frames } = fakeScreen();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await confirmedCapture(calls);
    frames.same = true;
    await act(async () => { vi.advanceTimersByTime(5000); });
    await act(async () => { vi.advanceTimersByTime(5000); });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("same-account duplicates skipped"));
    expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(2); // first + first "same" frame; then skipped
    expect(screen.getByRole("status")).toHaveTextContent("1 same-account duplicates skipped");
  });

  it("requires re-approval after a long capture gap", async () => {
    const { calls } = fakeBackend({ records: [] });
    fakeScreen();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await confirmedCapture(calls);
    vi.setSystemTime(Date.now() + 60_000); // computer slept / tab throttled
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(await screen.findByText(/Capture gap of \d+s detected/)).toHaveAttribute("role", "alert");
    expect(screen.getByRole("button", { name: "Confirm crop & retain frames" })).toBeEnabled();
  });

  it("does not sample a muted (minimized/hidden) source", async () => {
    const { calls } = fakeBackend({ records: [] });
    const { track } = fakeScreen();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await confirmedCapture(calls);
    track.muted = true;
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(await screen.findByText(/not delivering frames/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(1);
  });

  it("adopts a changed source size and resumes only after confirmation", async () => {
    const { calls } = fakeBackend({ records: [] });
    const { size } = fakeScreen();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await confirmedCapture(calls);
    size.width = 1280;
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(await screen.findByText("screen:0 · 1280 × 900 · Crop pixels")).toBeInTheDocument();
    expect(screen.getByLabelText("Crop x1")).toHaveValue(1280);
    expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Confirm crop & retain frames" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(2));
    expect(calls[calls.findLastIndex((c) => c.url === "/api/ocr/extract")].headers["x-crop"]).toBe("0,0,1280,900");
  });

  it("explains a cancelled or denied permission and leaves capture stopped", async () => {
    fakeBackend({ records: [] });
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getDisplayMedia: vi.fn(async () => { throw new DOMException("Permission denied", "NotAllowedError"); }) } });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    fireEvent.click(screen.getByRole("button", { name: /Share source/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Screen sharing was cancelled or not permitted. Nothing was captured.");
    expect(screen.getByRole("button", { name: /Share source/ })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("Ready");
  });
});

describe("LocalAutoSyncPanel — queue failure handling", () => {
  function sequence(responses: Response[]) {
    let i = 0;
    return async () => responses[Math.min(i++, responses.length - 1)];
  }
  const err = (code: string, status: number) => jsonResponse({ error: { code, message: `${code} message.` } }, status);

  it("keeps the screenshot and pauses when the OCR engine is unavailable", async () => {
    const { calls } = fakeBackend({ records: [], extract: sequence([err("OCR_ENGINE_UNAVAILABLE", 503), jsonResponse(sampleResponse, 201)]) });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile("keep-me.png")]);
    expect(await screen.findByText(/Remaining queue is paused/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Queue paused · 1 queued");
    fireEvent.click(screen.getByRole("button", { name: "Resume queue" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(2));
    expect(calls.filter((c) => c.url === "/api/ocr/extract").every((c) => c.headers["x-filename"] === "keep-me.png")).toBe(true);
  });

  it("drops only file-specific rejections and continues with the next screenshot", async () => {
    const { calls } = fakeBackend({ records: [], extract: sequence([err("CORRUPTED_IMAGE", 422), jsonResponse(sampleResponse, 201)]) });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile("bad.png"), pngFile("good.png")]);
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/ocr/extract")).toHaveLength(2));
    expect(screen.getByText(/bad.png: CORRUPTED_IMAGE message. This file was not accepted./)).toBeInTheDocument();
    expect(screen.getByRole("status")).not.toHaveTextContent("Queue paused");
  });

  it("points recorded OCR failures to Retry OCR", async () => {
    fakeBackend({ records: [], extract: async () => err("OCR_TIMEOUT", 504) });
    render(<LocalAutoSyncPanel />);
    await waitUntilReady();
    choose([pngFile("slow.png")]);
    expect(await screen.findByText(/slow.png: OCR_TIMEOUT message. The failed reading was saved; use Retry OCR on it./)).toBeInTheDocument();
  });

  it("explains a duplicate whose saved copy is in Trash", async () => {
    fakeBackend({ extract: async () => jsonResponse({ ...sampleResponse, duplicate: true, capture: { ...sampleResponse.capture, trashed: true } }) });
    render(<LocalAutoSyncPanel />);
    await screen.findByLabelText("Captured screenshot");
    choose([pngFile("again.png")]);
    expect(await screen.findByText(/again.png: identical screenshot is already saved but is in Trash/)).toBeInTheDocument();
  });

  it("saves pending corrections with keepalive when the page is hidden", async () => {
    const { fetchMock } = fakeBackend();
    render(<LocalAutoSyncPanel />);
    fireEvent.change(await screen.findByLabelText("Review OCR text"), { target: { value: "typed just before closing" } });
    window.dispatchEvent(new Event("pagehide"));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === "PATCH" && (init as RequestInit).keepalive === true)).toBe(true));
  });
});
