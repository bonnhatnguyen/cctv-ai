import { useEffect, useRef, useState } from "react";
import { AnnotationApiError, createSetup, newOperationId, saveRoi, saveTemplate } from "./api";
import { polygonError } from "./geometry";
import type { CameraSetupView, ClipView, Point } from "./types.generated";
import type { FrameToken } from "./useExactFrame";

export function RoiEditor({ clip, frameReady, displayed, points, setPoints, setups, setSetups, onSaved }: {
  clip: ClipView;
  frameReady: boolean;
  displayed: FrameToken | null;
  points: Point[];
  setPoints: React.Dispatch<React.SetStateAction<Point[]>>;
  setups: CameraSetupView[];
  setSetups: React.Dispatch<React.SetStateAction<CameraSetupView[]>>;
  onSaved: (clip: ClipView) => void;
}) {
  const [selected, setSelected] = useState(clip.roi?.camera_setup_id ?? "");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [closed, setClosed] = useState(Boolean(clip.roi));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [templatePreview, setTemplatePreview] = useState<string | null>(null);
  const roiOperation = useRef<{ key: string; id: string } | null>(null);
  const dirty = JSON.stringify(points) !== JSON.stringify(clip.roi?.polygon ?? []);
  const validation = closed ? polygonError(points) : "Hãy đóng vùng trước khi lưu.";

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) return;
      if (event.key === "Backspace" && !closed) {
        event.preventDefault();
        setPoints((current) => current.slice(0, -1));
      }
      if (event.key === "Escape") {
        setPoints(clip.roi?.polygon ?? []);
        setClosed(Boolean(clip.roi));
      }
      if (event.ctrlKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        document.getElementById("save-roi")?.click();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [clip.roi, closed, setPoints]);

  useEffect(() => {
    setSelected(clip.roi?.camera_setup_id ?? "");
    setClosed(Boolean(clip.roi));
    setTemplatePreview(null);
    roiOperation.current = null;
  }, [clip.id, clip.roi?.id]);

  const addSetup = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const result = await createSetup({ operation_id: newOperationId(), name });
      setSetups((current) => [...current, result]);
      setSelected(result.id);
      setCreating(false);
      setName("");
    } catch {
      setError("Không thể lưu camera.");
    } finally { setSaving(false); }
  };
  const persist = async () => {
    if (!selected || validation || !frameReady || !displayed || templatePreview) return;
    setSaving(true);
    setError(null);
    try {
      const setup = setups.find((item) => item.id === selected)!;
      const content = {
        expected_clip_revision: clip.revision,
        camera_setup_id: selected,
        polygon: points,
        template_revision_id: setup.template?.id ?? null,
      };
      const key = JSON.stringify(content);
      if (roiOperation.current?.key !== key) roiOperation.current = { key, id: newOperationId() };
      const result = await saveRoi(clip.id, { operation_id: roiOperation.current.id, ...content });
      roiOperation.current = null;
      onSaved(result);
    } catch (reason) {
      setError(reason instanceof AnnotationApiError && reason.status === 409
        ? "Có xung đột phiên bản. Bản vẽ đang được giữ; hãy tải lại clip rồi thử lại."
        : "Không thể lưu ROI. Bản vẽ vẫn được giữ nguyên.");
    } finally { setSaving(false); }
  };
  const persistTemplate = async () => {
    const setup = setups.find((item) => item.id === selected);
    if (!setup || polygonError(points)) return;
    setSaving(true);
    try {
      const updated = await saveTemplate(setup.id, {
        operation_id: newOperationId(), expected_setup_revision: setup.revision, polygon: points,
      });
      setSetups((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch { setError("Không thể lưu mẫu camera."); } finally { setSaving(false); }
  };
  const showTemplate = () => {
    const setup = setups.find((item) => item.id === selected);
    if (!setup?.template) return;
    setPoints(setup.template.polygon);
    setClosed(true);
    setTemplatePreview(setup.template.id);
    setError(null);
  };

  return <div className="roi-editor">
    <h3>ROI rổ tiền</h3>
    <p>Chỉ vẽ quanh vùng rổ tiền cố định. M1 chưa gán nhãn hành động.</p>
    <label>Camera <select value={selected} onChange={(event) => { setSelected(event.target.value); setTemplatePreview(null); }}>
      <option value="">Chọn camera</option>
      {setups.map((setup) => <option key={setup.id} value={setup.id}>{setup.name}</option>)}
    </select></label>
    <button type="button" onClick={() => setCreating(true)}>Tạo camera</button>
    {setups.find((item) => item.id === selected)?.template && <button type="button" onClick={showTemplate}>Xem mẫu camera</button>}
    {templatePreview && <div className="template-confirm"><p>Mẫu đang xem chưa phải ROI của clip.</p><button type="button" onClick={() => setTemplatePreview(null)}>Xác nhận ROI cho clip này</button></div>}
    {creating && <div className="inline-form">
      <label>Tên camera <input aria-label="Tên camera" value={name} onChange={(event) => setName(event.target.value)} /></label>
      <button type="button" disabled={saving || !name.trim()} onClick={() => void addSetup()}>Lưu camera</button>
    </div>}
    <div className="roi-actions">
      <button type="button" disabled={!frameReady || points.length < 3 || closed} onClick={() => setClosed(true)}>Đóng vùng</button>
      <button type="button" onClick={() => { setPoints([]); setClosed(false); setTemplatePreview(null); }}>Vẽ lại</button>
      <button type="button" onClick={() => { setPoints(clip.roi?.polygon ?? []); setClosed(Boolean(clip.roi)); setTemplatePreview(null); }}>Hủy thay đổi</button>
      <button id="save-roi" className="primary" type="button" disabled={saving || !dirty || !selected || Boolean(validation) || !frameReady || Boolean(templatePreview)} onClick={() => void persist()}>Lưu ROI</button>
      <button type="button" disabled={saving || !selected || Boolean(polygonError(points))} onClick={() => void persistTemplate()}>Lưu làm mẫu</button>
    </div>
    {validation && points.length > 0 && <small>{validation}</small>}
    {!frameReady && <p className="preview-note">Chờ ảnh chính xác tải xong và tạm dừng preview để sửa ROI.</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
