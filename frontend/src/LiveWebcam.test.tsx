import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveWebcam } from "./LiveWebcam";

describe("LiveWebcam Component", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/v1/live/webcam/status")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                running: false,
                source: "0",
                device_index: 0,
                device_name: "NVIDIA RTX 3080 Ti",
                people_count: 0,
                people_in_zone: 0,
                fps: 0.0,
                inference_ms: 0.0,
                frame_available: false,
                enable_traces: false,
                enable_basket: true,
                basket_state: "IDLE",
                hand_in_basket: false,
                evidence_count: 1,
              }),
          });
        }
        if (url.includes("/api/v1/live/evidence/list")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                items: [
                  {
                    id: "ev_test_001",
                    filename: "ev_test_001.jpg",
                    timestamp: "2026-09-13T22:30:00Z",
                    timestamp_ms: 1710345678000,
                    event_type: "RÚT TIỀN",
                    person_id: 1,
                    confidence: 0.95,
                    duration_seconds: 3.5,
                    image_url: "/api/v1/live/evidence/ev_test_001.jpg",
                    summary: "Người #1 rút tiền từ rổ (3.5s)",
                  },
                ],
              }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders terminal bar, controls, and evidence log", async () => {
    render(<LiveWebcam />);

    expect(screen.getByText(/Nguồn Camera:/i)).toBeInTheDocument();
    expect(screen.getByText(/Giám sát Quầy & Rổ Tiền/i)).toBeInTheDocument();
    expect(screen.getByText(/Nhật Ký Bằng Chứng Giao Dịch/i)).toBeInTheDocument();

    // Check camera controls
    expect(screen.getByText(/Lật gương/i)).toBeInTheDocument();
    expect(screen.getByText(/Lật dọc/i)).toBeInTheDocument();
    expect(screen.getByText(/Khung rổ tiền/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Chụp Bằng Chứng/i)[0]).toBeInTheDocument();
    expect(screen.getAllByText(/BẬT/i).length).toBeGreaterThan(0);

    // Wait for evidence item to appear
    await waitFor(() => {
      expect(screen.getByText(/RÚT TIỀN/i)).toBeInTheDocument();
    });
  });

  it("switches source to RTSP and displays RTSP input drawer", async () => {
    render(<LiveWebcam />);

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "rtsp" } });
    expect(screen.getByPlaceholderText(/rtsp:\/\//i)).toBeInTheDocument();
  });

  it("opens evidence detail modal when clicking an evidence card", async () => {
    render(<LiveWebcam />);

    await waitFor(() => {
      expect(screen.getByText(/RÚT TIỀN/i)).toBeInTheDocument();
    });

    const card = screen.getByText(/Người #1 rút tiền từ rổ/i).closest(".evidence-card");
    expect(card).toBeInTheDocument();
    if (card) {
      fireEvent.click(card);
      expect(screen.getByText(/Chi tiết Bằng chứng: RÚT TIỀN/i)).toBeInTheDocument();
      expect(screen.getByText(/Tải ảnh gốc JPEG/i)).toBeInTheDocument();
    }
  });

  it("supports video simulation mode and feature flag toggle with safe fallback", async () => {
    render(<LiveWebcam />);

    // Feature flag button should be visible
    const flagBtn = screen.getByTitle(/Tắt tính năng mô phỏng video file/i);
    expect(flagBtn).toBeInTheDocument();
    expect(screen.getByText(/Mô phỏng Video:/i)).toBeInTheDocument();

    // The "file" option should exist in camera select while flag is ON
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    const fileOption = screen.getByText(/Mô Phỏng Video File/i);
    expect(fileOption).toBeInTheDocument();

    // Switch to simulated video source
    fireEvent.change(select, { target: { value: "file" } });
    expect(select.value).toBe("file");

    // Drawer should appear
    expect(screen.getByText(/Mô phỏng Luồng CCTV từ Tệp Video/i)).toBeInTheDocument();
    expect(screen.getByText(/Lặp vô tận video/i)).toBeInTheDocument();
    expect(screen.getByText(/Chọn tệp MP4, AVI, MOV, MKV để tải lên/i)).toBeInTheDocument();
    expect(screen.getByText(/Chạy Mô Phỏng Video/i)).toBeInTheDocument();

    // Toggle flag OFF (Fallback test)
    fireEvent.click(flagBtn);

    // After turning off, source should automatically fallback to webcam0
    await waitFor(() => {
      expect(select.value).toBe("webcam0");
      expect(screen.queryByText(/Mô phỏng Luồng CCTV từ Tệp Video/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Mô Phỏng Video File/i)).not.toBeInTheDocument();
    });
  });
});

