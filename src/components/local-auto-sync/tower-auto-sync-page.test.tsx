import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TowerDetails } from "@/src/components/local-auto-sync/tower-auto-sync-page";
import { detail, sampleRows } from "@/src/test-fixtures/ocr";

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
