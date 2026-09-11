import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { ActionEditor } from "./ActionEditor";
import { emptyDraft, type ActionDraft } from "./actionRules";

afterEach(cleanup);

function Editor({ ready = true }: { ready?: boolean }) {
  const [draft, setDraft] = useState<ActionDraft>({
    ...emptyDraft(), interaction_id: "interaction", label: "hand_out",
  });
  return <ActionEditor draft={draft} onChange={setDraft} currentFrame={7}
    frameReady={ready} saving={false} editing={false} error={null}
    onSave={vi.fn()} onCancel={vi.fn()} />;
}

it("captures exact frames with shortcuts and ignores label shortcuts inside controls", () => {
  render(<Editor />);
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "c" });
  fireEvent.keyDown(window, { key: "o" });
  expect(screen.getByRole("button", { name: /Bắt đầu 7/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Qua biên 7/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Kết thúc 7/ })).toBeVisible();
  const object = screen.getByLabelText("Vật");
  object.focus();
  fireEvent.keyDown(object, { key: "1" });
  expect(screen.getByRole("button", { name: /Hand out/ })).toHaveAttribute("aria-pressed", "true");
});

it("disables frame capture until the exact image is ready", () => {
  render(<Editor ready={false} />);
  expect(screen.getByRole("button", { name: /Bắt đầu/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Qua biên/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Kết thúc/ })).toBeDisabled();
});
