import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

type JobStatus = "imported" | "queued" | "processing" | "ready" | "failed";
type Stage = "imported" | "queued" | "loading" | "tracking" | "encoding" | "validating" | "ready" | "failed";

const metadata = {
  size_bytes: 1_048_576,
  width: 1920,
  height: 1080,
  duration_ms: 12_500,
  fps_num: 25,
  fps_den: 1,
  frame_count_estimate: 313,
  codec: "h264",
  preview_supported: true,
  sample_aspect_ratio: "1:1",
};

function job(
  status: JobStatus = "imported",
  stage: Stage = status === "processing" ? "loading" : status,
  overrides: Record<string, unknown> = {},
) {
  return {
    id: "job-1",
    original_name: "cua-hang.mp4",
    status,
    stage,
    metadata,
    processed_frames: 0,
    total_frames_estimate: 313,
    tracking_percent: null,
    summary: null,
    failure_code: null,
    source_url: "/api/v1/jobs/job-1/source",
    result_url: null,
    created_at: "2026-09-09T08:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

class FakeXMLHttpRequest {
  static instances: FakeXMLHttpRequest[] = [];
  method = "";
  url = "";
  status = 0;
  responseText = "";
  sentBody: Document | XMLHttpRequestBodyInit | null = null;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  aborted = false;

  constructor() {
    FakeXMLHttpRequest.instances.push(this);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  send(body: Document | XMLHttpRequestBodyInit | null) {
    this.sentBody = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
  }

  respond(status: number, payload: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(payload);
    this.onload?.();
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function selectMp4(name = "cua-hang.mp4") {
  const file = new File([new Uint8Array(1_048_576)], name, { type: "video/mp4" });
  fireEvent.change(screen.getByLabelText("Chọn video MP4", { selector: "input" }), { target: { files: [file] } });
  return file;
}

async function finishImport(value = job()) {
  const request = FakeXMLHttpRequest.instances.at(-1)!;
  await act(async () => request.respond(201, value));
}

describe("luồng theo dõi người từ MP4", () => {
  beforeEach(() => {
    localStorage.clear();
    FakeXMLHttpRequest.instances = [];
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows local file details, upload progress, then only server-confirmed metadata", async () => {
    render(<App />);

    selectMp4();
    expect(screen.getByText("cua-hang.mp4")).toBeVisible();
    expect(screen.getByText("1,0 MB")).toBeVisible();
    expect(screen.queryByText("1920 × 1080")).not.toBeInTheDocument();

    const request = FakeXMLHttpRequest.instances[0];
    expect(request.method).toBe("POST");
    expect(request.url).toBe("/api/v1/jobs");
    await act(async () => request.progress(25, 100));
    expect(screen.getByText("Đang tải lên: 25%")).toBeVisible();

    await finishImport();
    expect(await screen.findByText("1920 × 1080")).toBeVisible();
    expect(screen.getByText("00:13")).toBeVisible();
    expect(screen.getByRole("button", { name: "Bắt đầu theo dõi người" })).toBeEnabled();
    expect(localStorage.getItem("v1-active-tracking-job")).toBe("job-1");
  });

  it("rejects a non-MP4 locally and lets the operator retry a failed import", async () => {
    render(<App />);
    const textFile = new File(["not a video"], "ghi-chu.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Chọn video MP4", { selector: "input" }), { target: { files: [textFile] } });
    expect(screen.getByRole("alert")).toHaveTextContent("Vui lòng chọn tệp MP4");
    expect(FakeXMLHttpRequest.instances).toHaveLength(0);

    selectMp4();
    await act(async () => FakeXMLHttpRequest.instances[0].respond(400, { detail: "khong_the_doc_video" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Không thể đọc video MP4 này");
    fireEvent.click(screen.getByRole("button", { name: "Thử tải lên lại" }));
    expect(FakeXMLHttpRequest.instances).toHaveLength(2);
    await act(async () => FakeXMLHttpRequest.instances[1].respond(201, job()));
    expect(await screen.findByRole("button", { name: "Bắt đầu theo dõi người" })).toBeEnabled();
  });

  it("requires an explicit start and prevents duplicate start submissions", async () => {
    let releaseStart!: (response: Response) => void;
    const startResponse = new Promise<Response>((resolve) => { releaseStart = resolve; });
    const fetchMock = vi.mocked(fetch).mockReturnValue(startResponse);
    render(<App />);
    selectMp4();
    await finishImport();
    expect(fetchMock).not.toHaveBeenCalled();

    const start = screen.getByRole("button", { name: "Bắt đầu theo dõi người" });
    fireEvent.click(start);
    fireEvent.click(start);
    expect(start).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs/job-1/start",
      expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }),
    );
    await act(async () => releaseStart(jsonResponse(job("queued"))));
    expect(await screen.findByText("Đang chờ đến lượt")).toBeVisible();
  });

  it("polls truthful stages and shows READY players plus measured metrics", async () => {
    vi.useFakeTimers();
    const ready = job("ready", "ready", {
      processed_frames: 313,
      tracking_percent: 100,
      result_url: "/api/v1/jobs/job-1/result",
      started_at: "2026-09-09T08:00:01Z",
      finished_at: "2026-09-09T08:00:11Z",
      summary: {
        actual_device: "cuda:0",
        device_name: "NVIDIA GeForce RTX 3060 Ti",
        processed_frames: 313,
        local_track_count: 4,
        inference_samples: 313,
        mean_inference_ms: 18.25,
        tracking_wall_ms_total: 7000,
        processing_seconds: 10.4,
        effective_fps: 30.1,
        output_duration_ms: 12520,
      },
    });
    const responses = [
      job("processing", "loading"),
      job("processing", "tracking", { processed_frames: 78, tracking_percent: 24.9 }),
      job("processing", "encoding", { processed_frames: 313, tracking_percent: 99.9 }),
      job("processing", "validating", { processed_frames: 313, tracking_percent: 99.9 }),
      ready,
    ];
    vi.mocked(fetch).mockImplementation(async (_url, init) => {
      if ((init as RequestInit | undefined)?.method === "POST") return jsonResponse(responses.shift()!);
      return jsonResponse(responses.shift()!);
    });
    render(<App />);
    selectMp4();
    await finishImport();
    fireEvent.click(screen.getByRole("button", { name: "Bắt đầu theo dõi người" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Đang tải mô hình")).toBeVisible();

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText("Đang theo dõi người")).toBeVisible();
    expect(screen.getByText("Tiến độ theo dõi ước tính: 24,9%")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText("Đang tạo video kết quả")).toBeVisible();
    expect(screen.queryByText(/Tiến độ theo dõi ước tính/)).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText("Đang kiểm tra video kết quả")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(1000));

    expect(screen.getByLabelText("Video gốc")).toHaveAttribute("src", "/api/v1/jobs/job-1/source");
    expect(screen.getByLabelText("Video đã theo dõi")).toHaveAttribute("src", "/api/v1/jobs/job-1/result");
    expect(screen.getByRole("link", { name: "Tải video kết quả" })).toHaveAttribute(
      "href", "/api/v1/jobs/job-1/result?download=1",
    );
    expect(screen.getByRole("button", { name: "Phát lại video kết quả" })).toBeVisible();
    expect(screen.getByText("cuda:0 · NVIDIA GeForce RTX 3060 Ti")).toBeVisible();
    expect(screen.getByText("313", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("4", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("10,4 giây")).toBeVisible();
    expect(screen.getByText("30,1 khung hình/giây")).toBeVisible();
    expect(screen.getByText("18,25 ms")).toBeVisible();
  });

  it("keeps polling when the backend remains in the same stage", async () => {
    vi.useFakeTimers();
    const loading = job("processing", "loading");
    const ready = job("ready", "ready", {
      processed_frames: 313,
      tracking_percent: 100,
      result_url: "/api/v1/jobs/job-1/result",
      summary: {
        actual_device: "cuda:0", device_name: "RTX 3060 Ti", processed_frames: 313,
        local_track_count: 3, inference_samples: 313, mean_inference_ms: 8,
        tracking_wall_ms_total: 8000, processing_seconds: 11, effective_fps: 28.5,
        output_duration_ms: 12520,
      },
    });
    const fetchMock = vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(loading))
      .mockResolvedValueOnce(jsonResponse(loading))
      .mockResolvedValueOnce(jsonResponse(ready));
    render(<App />);
    selectMp4();
    await finishImport();
    fireEvent.click(screen.getByRole("button", { name: "Bắt đầu theo dõi người" }));
    await act(async () => { await Promise.resolve(); });

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await act(async () => vi.advanceTimersByTimeAsync(1000));

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(screen.getByLabelText("Video đã theo dõi")).toBeVisible();
  });

  it("explains an unsupported original preview without treating analysis as failed", async () => {
    render(<App />);
    selectMp4();
    await finishImport(job("ready", "ready", {
      metadata: { ...metadata, preview_supported: false },
      result_url: "/api/v1/jobs/job-1/result",
      summary: {
        actual_device: "cpu", device_name: "CPU", processed_frames: 313,
        local_track_count: 2, inference_samples: 0, mean_inference_ms: null,
        tracking_wall_ms_total: 15000, processing_seconds: 18, effective_fps: 17.4,
        output_duration_ms: 12520,
      },
    }));
    expect(screen.getByText("Trình duyệt không hỗ trợ xem trước video gốc này.")).toBeVisible();
    expect(screen.getByLabelText("Video đã theo dõi")).toBeVisible();
    expect(screen.queryByText(/thời gian suy luận trung bình/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("restores an active job after refresh", async () => {
    localStorage.setItem("v1-active-tracking-job", "job-restored");
    vi.mocked(fetch).mockResolvedValue(jsonResponse(job("imported", "imported", {
      id: "job-restored",
      original_name: "da-luu.mp4",
      source_url: "/api/v1/jobs/job-restored/source",
    })));
    render(<App />);
    expect(await screen.findByText("da-luu.mp4")).toBeVisible();
    expect(screen.getByRole("button", { name: "Bắt đầu theo dõi người" })).toBeEnabled();
  });

  it("clears a missing restored job instead of spinning forever", async () => {
    localStorage.setItem("v1-active-tracking-job", "job-missing");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "job_not_found" }, 404));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Phiên xử lý trước không còn tồn tại");
    expect(localStorage.getItem("v1-active-tracking-job")).toBeNull();
    expect(screen.getByLabelText("Chọn video MP4", { selector: "input" })).toBeEnabled();
  });

  it("ignores a stale restore response after a new video is selected", async () => {
    localStorage.setItem("v1-active-tracking-job", "job-old");
    let releaseRestore!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(new Promise<Response>((resolve) => { releaseRestore = resolve; }));
    render(<App />);
    selectMp4("video-moi.mp4");
    await finishImport(job("imported", "imported", {
      id: "job-new",
      original_name: "video-moi.mp4",
      source_url: "/api/v1/jobs/job-new/source",
    }));
    await act(async () => releaseRestore(jsonResponse(job("ready", "ready", {
      id: "job-old",
      original_name: "video-cu.mp4",
      result_url: "/api/v1/jobs/job-old/result",
    }))));
    expect(screen.getByText("video-moi.mp4")).toBeVisible();
    expect(screen.queryByText("video-cu.mp4")).not.toBeInTheDocument();
    expect(localStorage.getItem("v1-active-tracking-job")).toBe("job-new");
  });
});
