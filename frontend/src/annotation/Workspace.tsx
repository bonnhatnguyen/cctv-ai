import { useEffect, useMemo, useState } from "react";
import { AnnotationApiError, getClip, getStorage, listClips, listSetups, newOperationId, registerClip, releaseClip, retryClip } from "./api";
import { ClipList } from "./ClipList";
import { FrameViewer } from "./FrameViewer";
import { RoiEditor } from "./RoiEditor";
import { ClipTracking } from "./ClipTracking";
import type { CameraSetupView, ClipView, Point, StorageView } from "./types.generated";
import { useExactFrame } from "./useExactFrame";

const POLL_MS = 400;

export function Workspace({ initialJobId, onBack }: { initialJobId?: string; onBack: () => void }) {
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
  const exact = useExactFrame(active?.id ?? "", active?.source_sha256 ?? null, index);
  const frameReady = useMemo(() => !playing && exact.displayed !== null && exact.displayed.index === index && exact.displayed.clipId === active?.id, [playing, exact.displayed, index, active?.id]);
  const dirty = Boolean(active) && JSON.stringify(points) !== JSON.stringify(active?.roi?.polygon ?? []);

  const mergeClip = (clip: ClipView) => {
    setClips((current) => current.some((item) => item.id === clip.id)
      ? current.map((item) => item.id === clip.id && clip.revision >= item.revision ? clip : item)
      : [clip, ...current]);
    setActive((current) => current?.id === clip.id && clip.revision >= current.revision ? clip : current);
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
    getStorage(controller.signal).then(setStorage).catch(() => setError("Không thể đọc dung lượng annotation cục bộ."));
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!active || !["preparing", "releasing"].includes(active.preparation_state)) return;
    const timer = window.setInterval(() => getClip(active.id).then(mergeClip).catch(() => setError("Mất kết nối khi cập nhật trạng thái clip.")), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active?.id, active?.preparation_state]);
  useEffect(() => {
    if (!active || ["preparing", "releasing"].includes(active.preparation_state)) return;
    getStorage().then(setStorage).catch(() => setError("Không thể cập nhật dung lượng annotation cục bộ."));
  }, [active?.revision, active?.preparation_state]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
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
    } catch { setError("Không thể mở clip để khoanh rổ tiền."); }
  };
  const release = async (clip: ClipView) => {
    if (!window.confirm("Chỉ xóa bản xem tạm và frame phát sinh; video nguồn cùng ROI vẫn được giữ. Tiếp tục?")) return;
    try { mergeClip(await releaseClip(clip.id, { operation_id: newOperationId(), expected_clip_revision: clip.revision })); }
    catch { setError("Không thể giải phóng bản xem tạm."); }
  };
  const retry = async (clip: ClipView) => {
    try { mergeClip(await retryClip(clip.id, { operation_id: newOperationId(), expected_clip_revision: clip.revision })); }
    catch (reason) { setError(reason instanceof AnnotationApiError && reason.status === 409 ? "Nguồn clip đã thay đổi hoặc revision không còn mới." : "Không thể chuẩn bị lại clip."); }
  };
  const more = async () => {
    if (!nextCursor) return;
    const page = await listClips(nextCursor);
    setClips((current) => [...current, ...page.items]);
    setNextCursor(page.next_cursor);
  };
  const open = (clip: ClipView) => {
    if (active?.id !== clip.id && dirty && !window.confirm("ROI chưa lưu sẽ bị bỏ. Mở clip khác?")) return;
    setActive(clip);
  };
  const back = () => {
    if (!dirty || window.confirm("ROI chưa lưu sẽ bị bỏ. Quay lại?")) onBack();
  };

  return <main className="annotation-workspace">
    <header className="workspace-header"><div><p className="eyebrow">V2 · dữ liệu riêng tư trên máy</p><h1>Khoanh ROI rổ tiền</h1></div><button type="button" className="secondary" onClick={back}>Quay lại theo dõi</button></header>
    {initialJobId && <section className="annotation-start"><p>Mở video vừa nhập để khoanh vùng cố định; không cần chạy person tracking.</p><button className="primary" type="button" onClick={() => void begin()}>Khoanh rổ tiền</button></section>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="workspace-grid">
      <div><ClipList clips={clips} activeId={active?.id} onOpen={open} onRelease={(clip) => void release(clip)} onRetry={(clip) => void retry(clip)} nextCursor={nextCursor} onMore={() => void more()} />
        {storage && <p className="storage-note">Dữ liệu phát sinh: {(storage.used_bytes / 1024 ** 3).toFixed(2)} / {(storage.limit_bytes / 1024 ** 3).toFixed(0)} GiB · đĩa trống {(storage.free_bytes / 1024 ** 3).toFixed(1)} GiB</p>}
      </div>
      <section className="annotation-main">
        {loading && <p>Đang tải danh sách clip…</p>}
        {!loading && !active && <p className="preview-note">Chọn một clip hoặc mở video vừa nhập.</p>}
        {active && <><h2>{active.original_name}</h2>
          {active.preparation_state === "preparing" && <p>Đang chuẩn bị frame chính xác và preview sạch…</p>}
          {active.preparation_state === "releasing" && <p>Đang giải phóng bản xem tạm…</p>}
          {active.preparation_state === "failed" && <p className="error">Chuẩn bị thất bại: {active.failure_code}</p>}
          {active.source_state !== "available" && <p className="error">Video nguồn không còn khớp clip này. ROI đã lưu vẫn được giữ.</p>}
          {active.preparation_state === "ready" && active.media && <>
            <FrameViewer key={active.id} clip={active} index={index} onIndex={selectIndex} candidate={exact.candidate} onFrameLoaded={exact.confirmLoaded} onFrameError={exact.rejectLoad} playing={playing} onPlaybackChange={setPlaying} points={points}
              onPoint={(point) => { if (frameReady) setPoints((current) => [...current, point]); }}
              onMovePoint={(position, point) => { if (frameReady) setPoints((current) => current.map((value, index) => index === position ? point : value)); }} />
            {exact.loading && <p>Đang tải frame {index}…</p>}{exact.error && <p className="error">{exact.error}</p>}
            <RoiEditor clip={active} frameReady={frameReady} displayed={exact.displayed} points={points} setPoints={setPoints} setups={setups} setSetups={setSetups} onSaved={(clip) => { mergeClip(clip); setPoints(clip.roi?.polygon ?? []); }} />
          </>}
          <ClipTracking clip={active} />
        </>}
      </section>
    </div>
  </main>;
}
