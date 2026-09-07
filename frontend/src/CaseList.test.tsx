import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CaseList } from "./CaseList";
it("shows a neutral review reason", async () => { vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [{ id: "1", status: "review_required", reason: "cash_removal_has_no_observed_destination", clips: {}, event_timeline: [] }] })); render(<CaseList onSelect={() => {}} />); expect(await screen.findByText("Tiền rời rổ chưa có điểm đến quan sát được")).toBeVisible(); });
