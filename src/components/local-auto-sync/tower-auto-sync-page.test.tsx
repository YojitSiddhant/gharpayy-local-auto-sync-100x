import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TowerAutoSyncPage, TowerDetails } from "@/src/components/local-auto-sync/tower-auto-sync-page";
import type { EvidenceDetail, EvidenceSummary } from "@/src/lib/local-evidence";
import { detail, jsonResponse, sampleRows, summary } from "@/src/test-fixtures/ocr";

const longMessage = "Looking for a twin sharing room near the metro with food included ".repeat(8).trim();
const rows = [...sampleRows, { ...sampleRows[0], position: 3, phone: { ...sampleRows[0].phone, normalized: "+919812345678" }, message_preview: longMessage, visible_timestamp_text: "Yesterday" }];

function renderDetails(overrides: Partial<typeof detail> = {}) {
  const onClose = vi.fn();
  render(<TowerDetails detail={{ ...detail, rows, ...overrides }} image="blob:screenshot" onClose={onClose} />);
  return onClose;
}

describe("TowerDetails", () => {
  it("renders extracted phone numbers, timestamps and previews as a chat list", () => {
    renderDetails();
    const list = within(screen.getByRole("list", { name: "Extracted chats" }));
    expect(screen.getByRole("dialog", { name: "Screenshot details" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Saved WhatsApp screenshot" })).toHaveAttribute("src", "blob:screenshot");
    expect(list.getAllByRole("listitem").filter((li) => li.parentElement?.getAttribute("aria-labelledby") === "tower-chats-title")).toHaveLength(3);
    expect(list.getByText("+919876543210")).toBeInTheDocument();
    expect(list.getByText("2:52 pm")).toBeInTheDocument();
    expect(list.getByText("Need a private room")).toBeInTheDocument();
    expect(screen.queryByText(/Last visible message:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Visible timestamp:/)).not.toBeInTheDocument();
  });

  it("shows missing values safely and keeps warnings available", () => {
    renderDetails();
    const ambiguous = screen.getByText("+91 9876O 43210").closest("li")!;
    expect(within(ambiguous).getByText("No time")).toBeInTheDocument();
    expect(within(ambiguous).getByText("Message not captured")).toBeInTheDocument();
    expect(within(ambiguous).getByText(/Needs review · 1 warning/)).toBeInTheDocument();
    expect(within(ambiguous).getByText("Phone-like header contains OCR-confusable characters; digits not repaired")).toBeInTheDocument();
    expect(within(ambiguous).getByText(/Phone reading: ambiguous/)).toBeInTheDocument();
    expect(within(screen.getByText("+919876543210").closest("li")!).queryByText(/Needs review/)).not.toBeInTheDocument();
  });

  it("truncates long messages without losing the full text", () => {
    renderDetails();
    const preview = screen.getByText(longMessage);
    expect(preview).toHaveClass("truncate");
    expect(preview).toHaveAttribute("title", longMessage);
  });

  it("handles screenshots with no extracted rows", () => {
    renderDetails({ rows: [] });
    expect(screen.getByText("No chat rows were read from this screenshot.")).toBeInTheDocument();
  });

  it.each([1, 3, 7, 15])("renders every row for %i extracted chats", (count) => {
    renderDetails({ rows: Array.from({ length: count }, (_, i) => ({ ...sampleRows[0], position: i + 1 })) });
    expect(screen.getByRole("list", { name: "Extracted chats" }).children).toHaveLength(count);
  });

  it("closes from the button and the Escape key", () => {
    const onClose = renderDetails();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

const cap = (n: number, status: EvidenceSummary["status"] = "review"): EvidenceSummary => ({ ...summary, id: `cap-${n}`, label: `tower-${n}.png`, status, captured_at: `2026-10-20T10:00:${String(n).padStart(2, "0")}Z` });
const phoneFor = (c: EvidenceSummary) => `+9198765432${c.id.replace(/\D/g, "").padStart(2, "0")}`;
const recordFor = (c: EvidenceSummary): EvidenceDetail => ({ ...detail, ...c, rows: [{ ...sampleRows[0], phone: { raw: phoneFor(c), normalized: phoneFor(c), status: "candidate" }, message_preview: `Message from ${c.id}` }] });

/** Fake same-origin local API. The list is returned newest first, like the backend. */
function fakeApi(initial: EvidenceSummary[]) {
  const state = { records: initial };
  const fetchMock = vi.fn(async (input: string) => {
    if (input === "/api/local/capture-sessions/current") return jsonResponse({ session: null });
    if (input === "/api/local/capture-sessions") return jsonResponse({ session: { id: "session-1", status: "active", zone: { id: "zone-1", name: "central students" }, expires_at: "2026-10-20T18:00:00Z" } });
    if (String(input).startsWith("/api/local/capture-sessions/session-1/stop")) return jsonResponse({ session: { id: "session-1", status: "stopped" } });
    const path = String(input).replace("/api/local/evidence", "");
    if (path.startsWith("?")) return jsonResponse({ items: [...state.records].reverse() });
    const image = path.match(/^\/(.+)\/image$/);
    if (image) return jsonResponse({});
    const record = state.records.find((r) => `/${r.id}` === path);
    return record ? jsonResponse(recordFor(record)) : jsonResponse({ error: { code: "NOT_FOUND", message: "nope" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, fetchMock };
}
const cards = () => within(screen.getByRole("list", { name: "Captured screenshots" })).getAllByRole("listitem");

describe("TowerAutoSyncPage capture strip and extracted chats", () => {
  const scrollTo = vi.fn();
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:thumb");
    URL.revokeObjectURL = vi.fn();
    scrollTo.mockReset();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("shows empty states with zero screenshots", async () => {
    fakeApi([]);
    render(<TowerAutoSyncPage />);
    expect(await screen.findByText("No saved screenshots yet.")).toBeInTheDocument();
    expect(screen.getByText("No screenshots captured yet.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Captured screenshots" })).not.toBeInTheDocument();
  });

  it("renders one screenshot card with order, time, status and View details", async () => {
    fakeApi([cap(1)]);
    render(<TowerAutoSyncPage />);
    await waitFor(() => expect(cards()).toHaveLength(1));
    const card = within(cards()[0]);
    expect(card.getByText("#1")).toBeInTheDocument();
    expect(card.getByText("Needs review")).toBeInTheDocument();
    expect(card.getByText(new Date("2026-10-20T10:00:01Z").toLocaleString())).toBeInTheDocument();
    expect(card.getByRole("button", { name: "View details" })).toBeInTheDocument();
    expect(await screen.findByText("Message from cap-1")).toBeInTheDocument();
  });

  it("lays screenshots out oldest-left in a single non-wrapping horizontal row", async () => {
    fakeApi([cap(1), cap(2, "reviewed"), cap(3, "error")]);
    render(<TowerAutoSyncPage />);
    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(cards().map((c) => c.getAttribute("aria-label"))).toEqual(["Screenshot #1", "Screenshot #2", "Screenshot #3"]);
    expect(within(cards()[0]).getByRole("button", { name: "Open screenshot #1" })).toBeInTheDocument();
    const strip = screen.getByTestId("capture-strip");
    expect(strip).toHaveClass("flex", "flex-nowrap", "overflow-x-auto");
    expect(strip).not.toHaveClass("grid");
    cards().forEach((c) => expect(c).toHaveClass("shrink-0", "w-44"));
    expect(within(cards()[1]).getByText("Reviewed")).toBeInTheDocument();
    expect(within(cards()[2]).getByText("OCR failed")).toBeInTheDocument();
  });

  it("appends arriving screenshots to the right and follows only when already at the end", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = fakeApi([cap(1, "processing")]);
    render(<TowerAutoSyncPage />);
    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(screen.getByRole("status")).toHaveTextContent("OCR still pending for 1 screenshot");
    // The initial jump to the newest card runs in a passive effect that can flush after the cards render (slow CI).
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" })));
    scrollTo.mockClear();
    api.state.records = [cap(1), cap(2, "processing")];
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(cards()[1]).toHaveAccessibleName("Screenshot #2");
    await waitFor(() => expect(scrollTo).toHaveBeenCalledTimes(1));
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: "smooth" }));
    const strip = screen.getByTestId("capture-strip");
    Object.defineProperties(strip, { scrollLeft: { value: 0, configurable: true }, clientWidth: { value: 400, configurable: true }, scrollWidth: { value: 1200, configurable: true } });
    fireEvent.scroll(strip);
    scrollTo.mockClear();
    api.state.records = [cap(1), cap(2), cap(3)];
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await waitFor(() => expect(cards()).toHaveLength(3));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Includes every screenshot shown above"));
    expect(screen.getByText("Message from cap-3")).toBeInTheDocument();
    // Checked only after later renders, so the arrival effect has certainly run and did not scroll.
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("opens the matching Screenshot details from a card, View details and a chat source link", async () => {
    fakeApi([cap(1), cap(2)]);
    render(<TowerAutoSyncPage />);
    await waitFor(() => expect(cards()).toHaveLength(2));
    fireEvent.click(within(cards()[1]).getByRole("button", { name: "Open screenshot #2" }));
    let dialog = await screen.findByRole("dialog", { name: "Screenshot details" });
    expect(within(dialog).getByText("Message from cap-2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(within(cards()[0]).getByRole("button", { name: "View details" }));
    dialog = await screen.findByRole("dialog", { name: "Screenshot details" });
    expect(within(dialog).getByText("Message from cap-1")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click((await screen.findAllByRole("button", { name: "Open screenshot #2 details" }))[0]);
    expect(within(await screen.findByRole("dialog")).getByText("Message from cap-2")).toBeInTheDocument();
  });

  it("holds the combined list while capturing and shows it after Stop", async () => {
    fakeApi([cap(1)]);
    const track = { stop: vi.fn(), addEventListener: vi.fn() };
    vi.stubGlobal("navigator", { ...window.navigator, mediaDevices: { getDisplayMedia: vi.fn(async () => ({ getTracks: () => [track], getVideoTracks: () => [track] })) } });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLVideoElement.prototype, "videoWidth", "get").mockReturnValue(1280);
    vi.spyOn(HTMLVideoElement.prototype, "videoHeight", "get").mockReturnValue(720);
    render(<TowerAutoSyncPage />);
    expect(await screen.findByText("Message from cap-1")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Start Auto Sync/ })));
    expect(await screen.findByRole("status")).toHaveTextContent(/Awaiting Stop/);
    expect(screen.getByRole("region", { name: "Live capture monitor" })).toBeInTheDocument();
    expect(screen.getByText("Monitoring")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "All extracted chats" })).not.toBeInTheDocument();
    expect(cards()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Message from cap-1")).toBeInTheDocument();
    expect(screen.queryByText(/Awaiting Stop/)).not.toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("shows previously completed captures' data after a reload without a new session", async () => {
    fakeApi([cap(1), cap(2)]);
    const first = render(<TowerAutoSyncPage />);
    await screen.findByText("Message from cap-2");
    first.unmount();
    render(<TowerAutoSyncPage />);
    expect(await screen.findByText("Message from cap-1")).toBeInTheDocument();
    expect(screen.getByText("Message from cap-2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start Auto Sync/ })).toBeInTheDocument();
  });
});
