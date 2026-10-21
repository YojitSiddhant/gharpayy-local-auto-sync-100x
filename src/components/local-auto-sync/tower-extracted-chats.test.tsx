import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceDetail, EvidenceSummary } from "@/src/lib/local-evidence";
import { detail as baseDetail, sampleRows, summary } from "@/src/test-fixtures/ocr";

const getEvidence = vi.fn<(id: string) => Promise<EvidenceDetail>>();
vi.mock("@/src/lib/local-evidence", () => ({ getEvidence: (id: string) => getEvidence(id) }));
const { AllExtractedChats, groupExtractedChats, orderCaptures } = await import("@/src/components/local-auto-sync/tower-extracted-chats");

type Row = EvidenceDetail["rows"][number];
const good = sampleRows[0], ambiguous = sampleRows[1];
const row = (position: number, patch: Partial<Row> = {}): Row => ({ ...good, position, ...patch });
const phoneRow = (position: number, normalized: string, patch: Partial<Row> = {}) => row(position, { phone: { raw: normalized, normalized, status: "candidate" }, display_header_text: normalized, ...patch });
const cap = (id: string, captured_at: string | null, status: EvidenceSummary["status"] = "review"): EvidenceSummary => ({ ...summary, id, captured_at, status });
const rec = (c: EvidenceSummary, rows: Row[]): EvidenceDetail => ({ ...baseDetail, ...c, rows });

describe("orderCaptures", () => {
  it("orders oldest first with stable id tie-breaks and missing times last", () => {
    const out = orderCaptures([cap("c", "2026-10-20T10:02:00Z"), cap("z", null), cap("b", "2026-10-20T10:00:00Z"), cap("a", "2026-10-20T10:00:00Z")]);
    expect(out.map((c) => [c.id, c.order])).toEqual([["a", 1], ["b", 2], ["c", 3], ["z", 4]]);
  });
});

describe("groupExtractedChats", () => {
  const [first, second] = orderCaptures([cap("s1", "2026-10-20T10:00:00Z"), cap("s2", "2026-10-20T10:00:10Z")]);

  it("groups a confirmed number across overlapping screenshots and keeps each source's message", () => {
    const entries = groupExtractedChats([
      { capture: second, detail: rec(second, [phoneRow(1, "+919876543210", { message_preview: "Second view", visible_timestamp_text: "2:55 pm" })]) },
      { capture: first, detail: rec(first, [phoneRow(1, "+919876543210", { message_preview: "First view", visible_timestamp_text: "2:52 pm" }), phoneRow(2, "+919811112222")]) },
    ]);
    expect(entries.map((e) => e.key)).toEqual(["+919876543210", "+919811112222"]);
    const grouped = entries[0];
    expect(grouped.kind === "number" && grouped.occurrences.map((o) => [o.capture.id, o.row.message_preview, o.row.visible_timestamp_text])).toEqual([["s1", "First view", "2:52 pm"], ["s2", "Second view", "2:55 pm"]]);
  });

  it("never merges ambiguous, conflicting or quarantined readings", () => {
    const entries = groupExtractedChats([
      { capture: first, detail: rec(first, [ambiguous, phoneRow(3, "+919876543210", { quarantined: true })]) },
      { capture: second, detail: rec(second, [{ ...ambiguous, position: 1 }, row(2, { phone: { raw: "+91 98765", normalized: null, status: "conflict" } })]) },
    ]);
    expect(entries).toHaveLength(4);
    expect(entries.every((e) => e.kind === "unconfirmed")).toBe(true);
  });

  it("does not treat numbers inside message previews as contact identities", () => {
    const entries = groupExtractedChats([{ capture: first, detail: rec(first, [row(1, { phone: { raw: null, normalized: null, status: "none" }, display_header_text: "Ravi", message_preview: "call me on +91 98765 43210" })]) }]);
    expect(entries).toEqual([expect.objectContaining({ kind: "unconfirmed" })]);
  });
});

describe("AllExtractedChats", () => {
  beforeEach(() => getEvidence.mockReset());
  const captures = orderCaptures([cap("s1", "2026-10-20T10:00:00Z"), cap("s2", "2026-10-20T10:00:10Z")]);
  const records: Record<string, EvidenceDetail> = {
    s1: rec(captures[0], [phoneRow(1, "+919876543210", { message_preview: "Need a room" }), ambiguous]),
    s2: rec(captures[1], [phoneRow(1, "+919876543210", { message_preview: null, visible_timestamp_text: null })]),
  };
  const serve = () => getEvidence.mockImplementation(async (id) => records[id]);

  it("shows an empty state with zero screenshots", () => {
    render(<AllExtractedChats captures={[]} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    expect(screen.getByText("No screenshots captured yet.")).toBeInTheDocument();
    expect(getEvidence).not.toHaveBeenCalled();
  });

  it("is held as awaiting Stop while capturing and loads nothing", () => {
    serve();
    render(<AllExtractedChats captures={captures} capturing pendingJobs={1} onOpen={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Awaiting Stop/);
    expect(screen.queryByRole("list", { name: "All extracted chats" })).not.toBeInTheDocument();
    expect(getEvidence).not.toHaveBeenCalled();
  });

  it("groups repeated numbers into one expandable chat, keeps ambiguous readings separate and links to sources", async () => {
    serve();
    const onOpen = vi.fn();
    render(<AllExtractedChats captures={captures} capturing={false} pendingJobs={0} onOpen={onOpen} />);
    const list = await screen.findByRole("list", { name: "All extracted chats" });
    const group = await within(list).findByRole("button", { name: "+919876543210, seen in 2 screenshots" });
    expect(within(list).getAllByText("+919876543210")).toHaveLength(1);
    expect(within(list).getByText("Seen in 2 screenshots")).toBeInTheDocument();
    expect(within(list).getByText("2 readings")).toBeInTheDocument();
    expect(within(list).getByText(/Visible messages differ across screenshots/)).toBeInTheDocument();
    expect(within(list).queryByText("Need a room")).not.toBeInTheDocument();
    expect(group).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(group);
    expect(group).toHaveAttribute("aria-expanded", "true");
    const observations = within(within(list).getByRole("list", { name: "Observations of +919876543210" })).getAllByRole("listitem");
    expect(observations).toHaveLength(2);
    expect(within(observations[0]).getByText("Need a room")).toBeInTheDocument();
    expect(within(observations[1]).getByText("Message not captured")).toBeInTheDocument();
    expect(within(observations[1]).getByText("No time")).toBeInTheDocument();
    expect(within(list).getByText("Number needs review")).toBeInTheDocument();
    expect(within(list).getByText(/OCR header: “\+91 9876O 43210”/)).toBeInTheDocument();
    expect(within(list).queryByText("Unverified OCR")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Includes every screenshot shown above");
    fireEvent.click(within(observations[1]).getByRole("button", { name: "Open screenshot #2 details" }));
    expect(onOpen).toHaveBeenCalledWith("s2");
    fireEvent.click(within(list).getByRole("button", { name: "Open number needs review in screenshot #1" }));
    expect(onOpen).toHaveBeenLastCalledWith("s1");
  });

  it("shows one shared preview when every grouped observation agrees", async () => {
    const same = { s1: rec(captures[0], [phoneRow(1, "+919876543210", { message_preview: "Same text", visible_timestamp_text: "9:01 am" })]), s2: rec(captures[1], [phoneRow(1, "+919876543210", { message_preview: "Same text", visible_timestamp_text: "9:01 am" })]) };
    getEvidence.mockImplementation(async (id) => same[id as "s1" | "s2"]);
    render(<AllExtractedChats captures={captures} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    const group = await screen.findByRole("button", { name: "+919876543210, seen in 2 screenshots" });
    expect(within(group).getByText("Same text")).toBeInTheDocument();
    expect(within(group).getByText("9:01 am")).toBeInTheDocument();
  });

  it("renders one extracted chat as a WhatsApp-style row that opens its screenshot", async () => {
    getEvidence.mockImplementation(async () => rec(captures[0], [phoneRow(1, "+919811112222", { message_preview: "Is the room free?", visible_timestamp_text: "Yesterday" })]));
    const onOpen = vi.fn();
    render(<AllExtractedChats captures={captures.slice(0, 1)} capturing={false} pendingJobs={0} onOpen={onOpen} />);
    const row = await screen.findByRole("button", { name: "Open +919811112222 in screenshot #1" });
    expect(within(row).getByText("Is the room free?")).toHaveClass("truncate");
    expect(within(row).getByText("Yesterday")).toBeInTheDocument();
    expect(row.closest("li")!.querySelector("[aria-hidden='true'].rounded-full")).toHaveClass("size-10", "sm:size-12");
    expect(screen.queryByText(/Needs review/)).not.toBeInTheDocument();
    fireEvent.click(row);
    expect(onOpen).toHaveBeenCalledWith("s1");
  });

  it("renders many chats inside an internally scrolling list", async () => {
    getEvidence.mockImplementation(async () => rec(captures[0], Array.from({ length: 25 }, (_, i) => phoneRow(i + 1, `+9198000000${String(i).padStart(2, "0")}`))));
    render(<AllExtractedChats captures={captures.slice(0, 1)} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    const list = await screen.findByRole("list", { name: "All extracted chats" });
    await waitFor(() => expect(list.children).toHaveLength(25));
    expect(list).toHaveClass("max-h-[70vh]", "overflow-y-auto");
  });

  it("labels a missing number without repairing it and keeps warnings expandable", async () => {
    getEvidence.mockImplementation(async () => rec(captures[0], [row(1, { phone: { raw: null, normalized: null, status: "none" }, display_header_text: null, message_preview: null, visible_timestamp_text: null, warnings: ["Header line not found"] })]));
    render(<AllExtractedChats captures={captures.slice(0, 1)} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    const item = (await screen.findByText("Number not captured")).closest("li")!;
    expect(within(item).getByText("Message not captured")).toBeInTheDocument();
    expect(within(item).getByText("No time")).toBeInTheDocument();
    const summary = within(item).getByText("Needs review · 1 warning");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    expect(within(item).getByText("Header line not found")).toBeInTheDocument();
    expect(within(item).getByText(/Phone reading: none/)).toBeInTheDocument();
  });

  it("switches between dark and light themes without resetting data, expansion or scroll", async () => {
    serve();
    render(<AllExtractedChats captures={captures} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    const panel = screen.getByTestId("chat-panel");
    const dark = screen.getByRole("button", { name: "Dark Mode" }), light = screen.getByRole("button", { name: "Light Mode" });
    expect(panel).toHaveAttribute("data-theme", "dark");
    expect(panel).toHaveClass("bg-[#111b21]");
    expect(dark).toHaveAttribute("aria-pressed", "true");
    const group = await screen.findByRole("button", { name: "+919876543210, seen in 2 screenshots" });
    fireEvent.click(group);
    const list = screen.getByRole("list", { name: "All extracted chats" });
    list.scrollTop = 120;
    const calls = getEvidence.mock.calls.length;
    fireEvent.click(light);
    expect(panel).toHaveAttribute("data-theme", "light");
    expect(panel).toHaveClass("bg-white");
    expect(panel).not.toHaveClass("bg-[#111b21]");
    expect(light).toHaveAttribute("aria-pressed", "true");
    expect(dark).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("list", { name: "All extracted chats" })).toBe(list);
    expect(list.scrollTop).toBe(120);
    expect(group).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Need a room")).toBeInTheDocument();
    expect(getEvidence.mock.calls.length).toBe(calls);
    expect(screen.getAllByTestId("chat-panel")).toHaveLength(1);
    fireEvent.click(dark);
    expect(panel).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("shows a pending indicator and never claims completeness while OCR is pending", async () => {
    serve();
    const withProcessing = orderCaptures([...captures, cap("s3", "2026-10-20T10:00:20Z", "processing")]);
    const { rerender } = render(<AllExtractedChats captures={withProcessing} capturing={false} pendingJobs={2} onOpen={vi.fn()} />);
    await screen.findByText("Seen in 2 screenshots");
    expect(screen.getByRole("status")).toHaveTextContent("OCR still pending for 3 screenshots");
    expect(screen.queryByText(/Includes every screenshot/)).not.toBeInTheDocument();
    expect(getEvidence).not.toHaveBeenCalledWith("s3");
    records.s3 = rec(withProcessing[2], [phoneRow(1, "+919800000003")]);
    rerender(<AllExtractedChats captures={orderCaptures([...captures, cap("s3", "2026-10-20T10:00:20Z", "review")])} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    expect(await screen.findByText("+919800000003")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Includes every screenshot shown above");
  });

  it("reloads a capture when its status changes and marks reviewed screenshots", async () => {
    serve();
    const { rerender } = render(<AllExtractedChats captures={captures.slice(0, 1)} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    await screen.findByText("Need a room");
    const reviewed = orderCaptures([cap("s1", "2026-10-20T10:00:00Z", "reviewed")]);
    records.s1 = { ...records.s1, status: "reviewed" };
    rerender(<AllExtractedChats captures={reviewed} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    expect(await screen.findAllByText("Screenshot reviewed")).toHaveLength(2);
    expect(getEvidence).toHaveBeenCalledTimes(2);
  });

  it("reports unavailable records as incomplete and retries", async () => {
    getEvidence.mockRejectedValueOnce(new Error("down")).mockImplementation(async (id) => records[id]);
    render(<AllExtractedChats captures={captures.slice(0, 1)} capturing={false} pendingJobs={0} onOpen={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("this list is incomplete");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Retry" })));
    expect(await screen.findByText("Need a room")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
