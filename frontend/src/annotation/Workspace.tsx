import { useEffect, useMemo, useRef, useState } from "react";
import {
  AnnotationApiError,
  deleteClip,
  getClip,
  getStorage,
  listClips,
  listSetups,
  newOperationId,
  registerClip,
  releaseClip,
  retryClip,
} from "./api";
import { ClipList } from "./ClipList";
import { FrameViewer } from "./FrameViewer";
import { RoiEditor } from "./RoiEditor";
import { ClipTracking } from "./ClipTracking";
import { ActionWorkspace } from "./ActionWorkspace";
import type { CameraSetupView, ClipView, Point, StorageView } from "./types.generated";
import { useExactFrame } from "./useExactFrame";
import { ConfirmModal, ConfirmModalProps } from "../ConfirmModal";
import { importVideo, JobView } from "../trackingApi";

const POLL_MS = 400;

interface WorkspaceProps {
  initialJobId?: string;
  onBack: () => void;
  onJobCreated?: (job: JobView) => void;
}

export function Workspace({ initialJobId, onBack, onJobCreated }: WorkspaceProps) {
  const [clips, setClips] = useState<ClipView[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [active, setActive] = useState<ClipView | null>(null);
  const [setups, setSetups] = useState<CameraSetupView[]>([]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [points, setPoints] = useState<Point[]>([]);
  const [loading, setLoading] = useState(true);
  const [storage, setStorage] = useState<StorageView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"roi" | "annotate" | "review">("roi");
  const [actionDirty, setActionDirty] = useState(false);

  // Direct MP4 Upload in Workspace
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);

  // Confirm Modal state
  const [confirmModal, setConfirmModal] = useState<ConfirmModalProps>({
    isOpen: false,
    title: "",
    message: "",
    confirmText: "Xác nhận",
    cancelText: "Hủy bỏ",
    kind: "primary",
    onConfirm: () => {},
    onCancel: () => {},
  });

  const askConfirm = (options: {
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    kind?: "danger" | "primary";
    onConfirm: () => void;
  }) => {
    setConfirmModal({
      isOpen: true,
      title: options.title,
      message: options.message,
      confirmText: options.confirmText ?? "Xác nhận",
      cancelText: options.cancelText ?? "Hủy bỏ",
      kind: options.kind ?? "primary",
      onConfirm: () => {
        setConfirmModal((prev) => ({ ...prev, isOpen: false }));
        options.onConfirm();
      },
      onCancel: () => {
        setConfirmModal((prev) => ({ ...prev, isOpen: false }));
      },
    });
  };

  const exact = useExactFrame(active?.id ?? "", active?.source_sha256 ?? null, index);
  const frameReady = useMemo(
    () =>
      !playing &&
      exact.displayed !== null &&
      exact.displayed.index === index &&
      exact.displayed.clipId === active?.id,
    [playing, exact.displayed, index, active?.id]
  );
  const roiDirty = Boolean(active) && JSON.stringify(points) !== JSON.stringify(active?.roi?.polygon ?? []);
  const dirty = roiDirty || actionDirty;

  const mergeClip = (clip: ClipView) => {
    setClips((current) =>
      current.some((item) => item.id === clip.id)
        ? current.map((item) => (item.id === clip.id && clip.revision >= item.revision ? clip : item))
        : [clip, ...current]
    );
    setActive((current) => (current?.id === clip.id && clip.revision >= current.revision ? clip : current));
  };

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([listClips(null, controller.signal), listSetups(controller.signal)])
      .then(([clipPage, setupPage]) => {
        setClips(clipPage.items);
        setNextCursor(clipPage.next_cursor);
        setSetups(setupPage.items);
      })
      .catch(() => setError("Không thể tải danh sách annotation cục bộ."))
      .finally(() => setLoading(false));
    getStorage(controller.signal)
      .then(setStorage)
      .catch(() => setError("Không thể đọc dung lượng annotation cục bộ."));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!active || !["preparing", "releasing"].includes(active.preparation_state)) return;
    const timer = window.setInterval(
      () =>
        getClip(active.id)
          .then(mergeClip)
          .catch(() => setError("Mất kết nối khi cập nhật trạng thái clip.")),
      POLL_MS
    );
    return () => window.clearInterval(timer);
  }, [active?.id, active?.preparation_state]);

  useEffect(() => {
    if (!active || ["preparing", "releasing"].includes(active.preparation_state)) return;
    getStorage()
      .then(setStorage)
      .catch(() => setError("Không thể cập nhật dung lượng annotation cục bộ."));
  }, [active?.revision, active?.preparation_state]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    if (!active?.media) return;
    const saved = Number(localStorage.getItem(`v2-annotation-frame:${active.id}`));
    const initial = Number.isInteger(saved) ? Math.max(0, Math.min(active.media.frame_count - 1, saved)) : 0;
    setIndex(initial);
    setPoints(active.roi?.polygon ?? []);
    setPlaying(false);
    setMode(active.roi ? "annotate" : "roi");
    setActionDirty(false);
  }, [active?.id]);

  const selectIndex = (value: number) => {
    setIndex(value);
    if (active) localStorage.setItem(`v2-annotation-frame:${active.id}`, String(value));
  };

  const begin = async () => {
    if (!initialJobId) return;
    setError(null);
    try {
      const clip = await registerClip({ operation_id: newOperationId(), source_job_id: initialJobId });
      mergeClip(clip);
      setActive(clip);
    } catch {
      setError("Không thể mở clip để khoanh rổ tiền.");
    }
  };

  // Direct video upload handler inside Workspace
  const handleDirectUpload = (file: File) => {
    if (uploading) return;
    if (!file.name.toLowerCase().endsWith(".mp4")) {
      setError("Vui lòng chọn tệp video định dạng MP4.");
      return;
    }
    setError(null);
    setUploading(true);
    setUploadPercent(null);

    const controller = new AbortController();
    uploadAbort.current = controller;

    importVideo(file, setUploadPercent, controller.signal)
      .then(async (jobView) => {
        onJobCreated?.(jobView);
        // Automatically register clip for annotation
        const clip = await registerClip({
          operation_id: newOperationId(),
          source_job_id: jobView.id,
        });
        mergeClip(clip);
        setActive(clip);
        setMode("roi");
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError("Không thể tải lên hoặc nạp video này. Vui lòng kiểm tra lại file.");
      })
      .finally(() => {
        setUploading(false);
        setUploadPercent(null);
        uploadAbort.current = null;
      });
  };

  const release = (clip: ClipView) => {
    askConfirm({
      title: "Giải phóng bản xem tạm?",
      message: "Chỉ xóa bản xem tạm và frame phát sinh trên đĩa để tiết kiệm bộ nhớ; video nguồn cùng ROI đã lưu vẫn được giữ nguyên.",
      confirmText: "Giải phóng",
      kind: "primary",
      onConfirm: async () => {
        try {
          mergeClip(
            await releaseClip(clip.id, {
              operation_id: newOperationId(),
              expected_clip_revision: clip.revision,
            })
          );
        } catch {
          setError("Không thể giải phóng bản xem tạm.");
        }
      },
    });
  };

  const handleDeleteClip = (clip: ClipView) => {
    askConfirm({
      title: "Xác nhận xóa clip?",
      message: `Xóa vĩnh viễn clip "${clip.original_name}" và toàn bộ dữ liệu ROI/nhãn liên quan? Thao tác này không thể hoàn tác.`,
      confirmText: "Xóa vĩnh viễn",
      kind: "danger",
      onConfirm: async () => {
        try {
          await deleteClip(clip.id);
          setClips((current) => current.filter((item) => item.id !== clip.id));
          if (active?.id === clip.id) {
            setActive(null);
          }
          getStorage().then(setStorage).catch(() => {});
        } catch {
          setError("Không thể xóa clip. Hãy thử lại.");
        }
      },
    });
  };

  const retry = async (clip: ClipView) => {
    try {
      mergeClip(
        await retryClip(clip.id, {
          operation_id: newOperationId(),
          expected_clip_revision: clip.revision,
        })
      );
    } catch (reason) {
      setError(
        reason instanceof AnnotationApiError && reason.status === 409
          ? "Nguồn clip đã thay đổi hoặc revision không còn mới."
          : "Không thể chuẩn bị lại clip."
      );
    }
  };

  const more = async () => {
    if (!nextCursor) return;
    const page = await listClips(nextCursor);
    setClips((current) => [...current, ...page.items]);
    setNextCursor(page.next_cursor);
  };

  const open = (clip: ClipView) => {
    if (active?.id === clip.id) return;
    if (dirty) {
      askConfirm({
        title: "Bỏ thay đổi chưa lưu?",
        message: roiDirty
          ? "Tọa độ ROI chưa lưu sẽ bị hủy bỏ nếu bạn chuyển sang clip khác."
          : "Nhãn thao tác chưa lưu sẽ bị hủy bỏ nếu bạn chuyển sang clip khác.",
        confirmText: "Bỏ thay đổi",
        kind: "danger",
        onConfirm: () => setActive(clip),
      });
      return;
    }
    setActive(clip);
  };

  const back = () => {
    if (dirty) {
      askConfirm({
        title: "Bỏ thay đổi chưa lưu?",
        message: "Các thay đổi chưa lưu sẽ bị hủy bỏ khi bạn quay lại màn hình theo dõi.",
        confirmText: "Quay lại",
        kind: "danger",
        onConfirm: onBack,
      });
      return;
    }
    onBack();
  };

  const changeMode = (next: typeof mode) => {
    if (next === mode) return;
    if (dirty) {
      askConfirm({
        title: "Bỏ thay đổi chưa lưu?",
        message: "Thay đổi chưa lưu ở bước hiện tại sẽ bị hủy bỏ khi chuyển bước.",
        confirmText: "Chuyển bước",
        kind: "danger",
        onConfirm: () => setMode(next),
      });
      return;
    }
    setMode(next);
  };

  return (
    <main className="annotation-workspace">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Hiệu chỉnh rổ tiền · Dữ liệu an toàn trên máy</p>
          <h1>Gán nhãn hành động quanh rổ</h1>
        </div>
        <button type="button" className="secondary" onClick={back}>
          Quay lại theo dõi
        </button>
      </header>

      {/* Direct MP4 Upload Dropzone inside Workspace */}
      <section className="workspace-upload-bar">
        <label className={`workspace-upload-dropzone${uploading ? " disabled" : ""}`}>
          <input
            type="file"
            accept="video/mp4,.mp4"
            disabled={uploading}
            onClick={(e) => {
              e.currentTarget.value = "";
            }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleDirectUpload(file);
            }}
          />
          <div className="workspace-upload-content">
            <span className="upload-icon-small">📁</span>
            {uploading ? (
              <div className="upload-progress-inline">
                {uploadPercent === 100 ? (
                  <span>
                    <span className="upload-spinner" /> Đang kiểm tra & nạp video trên máy chủ...
                  </span>
                ) : (
                  <span>Đang tải lên: {uploadPercent ?? 0}%</span>
                )}
                <progress value={uploadPercent ?? 0} max={100} />
              </div>
            ) : (
              <div>
                <strong>Tải lên video MP4 mới</strong>
                <small> — Chọn hoặc kéo thả video vào đây để khoanh rổ tiền ngay lập tức</small>
              </div>
            )}
          </div>
        </label>
      </section>

      {initialJobId && !active && (
        <section className="annotation-start">
          <p>Mở video vừa nạp từ màn hình theo dõi để khoanh vùng cố định.</p>
          <button className="primary" type="button" onClick={() => void begin()}>
            Khoanh rổ tiền
          </button>
        </section>
      )}

      {error && <p className="error" role="alert">{error}</p>}

      <div className="workspace-grid">
        <div>
          <ClipList
            clips={clips}
            activeId={active?.id}
            onOpen={open}
            onRelease={release}
            onRetry={(clip) => void retry(clip)}
            onDelete={handleDeleteClip}
            nextCursor={nextCursor}
            onMore={() => void more()}
          />
          {storage && (
            <p className="storage-note">
              Dữ liệu phát sinh: {(storage.used_bytes / 1024 ** 3).toFixed(2)} /{" "}
              {(storage.limit_bytes / 1024 ** 3).toFixed(0)} GiB · đĩa trống{" "}
              {(storage.free_bytes / 1024 ** 3).toFixed(1)} GiB
            </p>
          )}
        </div>

        <section className="annotation-main">
          {loading && <p>Đang tải danh sách clip…</p>}
          {!loading && !active && (
            <p className="preview-note">Chọn một clip từ danh sách bên trái hoặc tải lên video MP4 mới ở trên.</p>
          )}

          {active && (
            <>
              <h2>{active.original_name}</h2>
              <nav className="workspace-steps" aria-label="Các bước annotation">
                <button
                  type="button"
                  aria-current={mode === "roi" ? "step" : undefined}
                  onClick={() => changeMode("roi")}
                >
                  <span>1</span>Vùng rổ
                </button>
                <button
                  type="button"
                  aria-current={mode === "annotate" ? "step" : undefined}
                  disabled={!active.roi}
                  onClick={() => changeMode("annotate")}
                >
                  <span>2</span>Gán nhãn
                </button>
                <button
                  type="button"
                  aria-current={mode === "review" ? "step" : undefined}
                  disabled={!active.roi}
                  onClick={() => changeMode("review")}
                >
                  <span>3</span>Kiểm tra
                </button>
              </nav>

              {active.preparation_state === "preparing" && (
                <p>Đang chuẩn bị frame chính xác và preview sạch…</p>
              )}
              {active.preparation_state === "releasing" && <p>Đang giải phóng bản xem tạm…</p>}
              {active.preparation_state === "failed" && (
                <p className="error">Chuẩn bị thất bại: {active.failure_code}</p>
              )}
              {active.source_state !== "available" && (
                <p className="error">Video nguồn không còn khớp clip này. ROI đã lưu vẫn được giữ.</p>
              )}

              {active.preparation_state === "ready" && active.media && (
                <>
                  <FrameViewer
                    key={active.id}
                    clip={active}
                    index={index}
                    onIndex={selectIndex}
                    candidate={exact.candidate}
                    onFrameLoaded={exact.confirmLoaded}
                    onFrameError={exact.rejectLoad}
                    playing={playing}
                    onPlaybackChange={setPlaying}
                    points={points}
                    editable={mode === "roi"}
                    onPoint={(point) => {
                      if (frameReady && mode === "roi") setPoints((current) => [...current, point]);
                    }}
                    onMovePoint={(position, point) => {
                      if (frameReady && mode === "roi") {
                        setPoints((current) =>
                          current.map((value, idx) => (idx === position ? point : value))
                        );
                      }
                    }}
                  />
                  {exact.loading && <p>Đang tải frame {index}…</p>}
                  {exact.error && <p className="error">{exact.error}</p>}
                  {mode === "roi" && (
                    <RoiEditor
                      clip={active}
                      frameReady={frameReady}
                      displayed={exact.displayed}
                      points={points}
                      setPoints={setPoints}
                      setups={setups}
                      setSetups={setSetups}
                      onSaved={(clip) => {
                        mergeClip(clip);
                        setPoints(clip.roi?.polygon ?? []);
                        setMode("annotate");
                      }}
                    />
                  )}
                  {mode !== "roi" && active.roi && (
                    <ActionWorkspace
                      key={`${active.id}:${mode}`}
                      clip={active}
                      index={index}
                      onIndex={(value) => {
                        setPlaying(false);
                        selectIndex(value);
                      }}
                      frameReady={frameReady}
                      reviewOnly={mode === "review"}
                      onDirtyChange={setActionDirty}
                      onClipRevision={(revision) => mergeClip({ ...active, revision })}
                      onClipReload={(latest) => {
                        mergeClip(latest);
                        setPoints(latest.roi?.polygon ?? []);
                        setPlaying(false);
                      }}
                    />
                  )}
                </>
              )}
              {mode === "roi" && <ClipTracking clip={active} />}
            </>
          )}
        </section>
      </div>

      {/* Apple macOS Dark Theme Confirm Modal */}
      <ConfirmModal {...confirmModal} />
    </main>
  );
}
