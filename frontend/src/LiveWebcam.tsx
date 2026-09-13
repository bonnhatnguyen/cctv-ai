import { ConfirmModal, ConfirmModalProps } from "./ConfirmModal";
import React, { useState, useEffect, useRef } from "react";

interface EvidenceItem {
  id: string;
  filename: string;
  image_url: string;
  event_type: string;
  timestamp: string;
  confidence: number;
  duration_seconds: number;
  summary: string;
}

type Point = [number, number];

const DEFAULT_BASKET_ROI: Point[] = [
  [190, 270],
  [450, 270],
  [490, 450],
  [150, 450],
];

const DEFAULT_ZONE_ROI: Point[] = [
  [160, 160],
  [480, 160],
  [520, 440],
  [120, 440],
];

const PRESETS_BASKET: Record<string, { label: string; points: Point[] }> = {
  center: {
    label: "Chuẩn Trung Tâm",
    points: [
      [190, 270],
      [450, 270],
      [490, 450],
      [150, 450],
    ],
  },
  left: {
    label: "Quầy Thu Ngân Trái",
    points: [
      [40, 270],
      [300, 270],
      [330, 455],
      [20, 455],
    ],
  },
  right: {
    label: "Quầy Thu Ngân Phải",
    points: [
      [340, 270],
      [600, 270],
      [620, 455],
      [310, 455],
    ],
  },
  wide: {
    label: "Rổ Tiền Mở Rộng",
    points: [
      [120, 220],
      [520, 220],
      [560, 465],
      [80, 465],
    ],
  },
};

const getRoiDimensions = (roi: Point[]) => {
  if (!roi || roi.length < 4) return { cx: 320, cy: 360, w: 300, h: 180 };
  const minX = Math.min(...roi.map((p) => p[0]));
  const maxX = Math.max(...roi.map((p) => p[0]));
  const minY = Math.min(...roi.map((p) => p[1]));
  const maxY = Math.max(...roi.map((p) => p[1]));
  return {
    cx: Math.round((minX + maxX) / 2),
    cy: Math.round((minY + maxY) / 2),
    w: Math.round(maxX - minX),
    h: Math.round(maxY - minY),
  };
};

export const LiveWebcam: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Connection & Source
  const [running, setRunning] = useState(false);
  const [sourceType, setSourceType] = useState<"webcam0" | "webcam1" | "rtsp" | "file">("webcam0");
  const [rtspUrl, setRtspUrl] = useState("rtsp://admin:password@192.168.1.100:554/ch0_0.264");
  const [showRtspInput, setShowRtspInput] = useState(false);
  const [loading, setLoading] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const [renderMode, setRenderMode] = useState<"stream" | "polling">("stream");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [streamFailed, setStreamFailed] = useState(false);

  // Feature Flag: Enable Simulated Video Mode (with safe fallback)
  const [enableVideoSim, setEnableVideoSim] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem("cctv_live_enable_video_simulation");
      if (saved !== null) return saved === "true";
    } catch {}
    return true;
  });

  interface SimVideoItem {
    video_id: string;
    filename: string;
    filepath: string;
    size_bytes: number;
    duration_sec: number;
    fps: number;
    width: number;
    height: number;
    created_at: string;
  }

  const [simVideos, setSimVideos] = useState<SimVideoItem[]>([]);
  const [selectedSimVideo, setSelectedSimVideo] = useState<SimVideoItem | null>(null);
  const [loopVideo, setLoopVideo] = useState<boolean>(true);
  const [uploadingVideo, setUploadingVideo] = useState<boolean>(false);

  // Camera Orientation with persistent local storage
  const [flipH, setFlipH] = useState(() => {
    try {
      const v = localStorage.getItem("cctv_live_flip_h");
      return v !== null ? v === "true" : true;
    } catch { return true; }
  });
  const [flipV, setFlipV] = useState(() => {
    try {
      const v = localStorage.getItem("cctv_live_flip_v");
      return v !== null ? v === "true" : false;
    } catch { return false; }
  });

  // Monitoring Modes
  const [isFocusedZoom, setIsFocusedZoom] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [basketEnabled, setBasketEnabled] = useState(() => {
    try {
      const v = localStorage.getItem("cctv_live_enable_basket");
      return v !== null ? v === "true" : true;
    } catch { return true; }
  });
  const [zoneEnabled, setZoneEnabled] = useState(() => {
    try {
      const v = localStorage.getItem("cctv_live_enable_zone");
      return v !== null ? v === "true" : true;
    } catch { return true; }
  });

  // ROI Geometry Editor with persistent local storage
  const [showRoiPanel, setShowRoiPanel] = useState(false);
  const [roiTarget, setRoiTarget] = useState<"basket" | "security_zone">("basket");
  const [basketRoi, setBasketRoi] = useState<Point[]>(() => {
    try {
      const v = localStorage.getItem("cctv_live_basket_roi");
      if (v) return JSON.parse(v);
    } catch {}
    return DEFAULT_BASKET_ROI;
  });
  const [zoneRoi, setZoneRoi] = useState<Point[]>(() => {
    try {
      const v = localStorage.getItem("cctv_live_zone_roi");
      if (v) return JSON.parse(v);
    } catch {}
    return DEFAULT_ZONE_ROI;
  });
  const [activeHandleIndex, setActiveHandleIndex] = useState<number | null>(null);
  // Confirm Modal state
  // AI Model, Precision & Skeleton
  const [modelMode, setModelMode] = useState<"yolo_pose" | "yolo_detect">(() => {
    try {
      const v = localStorage.getItem("cctv_live_model_mode");
      if (v === "yolo_pose" || v === "yolo_detect") return v;
    } catch {}
    return "yolo_pose";
  });
  const [precisionMode, setPrecisionMode] = useState<"cuda_fp16" | "onnx" | "cpu">(() => {
    try {
      const v = localStorage.getItem("cctv_live_precision_mode");
      if (v === "cuda_fp16" || v === "onnx" || v === "cpu") return v;
    } catch {}
    return "cuda_fp16";
  });
  const [enableSkeleton, setEnableSkeleton] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("cctv_live_enable_skeleton");
      if (v !== null) return v === "true";
    } catch {}
    return true;
  });
  const [showAiModal, setShowAiModal] = useState(false);

  // Telegram Bot Alert Config (Exclusively locked to @diep_nguyenk5 - 8269826134)
  const [telegramEnabled, setTelegramEnabled] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("cctv_live_telegram_enabled");
      if (v !== null) return v === "true";
    } catch {}
    return true;
  });
  const [telegramChatId, setTelegramChatId] = useState<string>("8269826134");
  const [telegramEvents, setTelegramEvents] = useState<string[]>(["RÚT TIỀN", "BỎ TIỀN", "CHẠM RỔ"]);
  const [showTelegramModal, setShowTelegramModal] = useState(false);
  const showTelegramModalRef = useRef(showTelegramModal);
  showTelegramModalRef.current = showTelegramModal;
  const [testingTelegram, setTestingTelegram] = useState(false);

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

  const [roiSaving, setRoiSaving] = useState(false);

  // Status state
  const [status, setStatus] = useState<{
    fps?: number;
    inference_ms?: number;
    people_count?: number;
    people_in_zone?: number;
    people_in_zone_ids?: number[];
    device_name?: string;
    basket_state?: string;
    hand_in_basket?: boolean;
    basket_roi?: Point[];
    security_zone_roi?: Point[];
    evidence_count?: number;
    model_mode?: "yolo_pose" | "yolo_detect";
    precision_mode?: "cuda_fp16" | "onnx" | "cpu";
    enable_skeleton?: boolean;
    hand_gesture?: string;
    is_pinching?: boolean;
    detected_currency?: string | null;
    hand_landmarks_count?: number;
    advanced_ai_models?: string[];
    telegram?: {
      configured: boolean;
      enabled: boolean;
      bot_username: string;
      has_token: boolean;
      chat_id: string;
      events: string[];
      cooldown_seconds: number;
    };
    last_error?: string | null;
  }>({});

  // Evidence Management
  const [evidenceList, setEvidenceList] = useState<EvidenceItem[]>([]);
  const [selectedFilter, setSelectedFilter] = useState<string>("ALL");
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceItem | null>(null);
  const [capturingSnapshot, setCapturingSnapshot] = useState(false);
  const [toastMessage, setToastMessage] = useState<{ text: string; type: "success" | "info" } | null>(null);

  const showToast = (text: string, type: "success" | "info" = "success") => {
    setToastMessage({ text, type });
    setTimeout(() => setToastMessage(null), 3200);
  };

  // Fetch Evidence List
  const fetchEvidence = async () => {
    try {
      const res = await fetch("/api/v1/live/evidence/list");
      if (res.ok) {
        const data = await res.json();
        setEvidenceList(data.items || []);
      }
    } catch {
      // ignore
    }
  };

  // Poll status every 1000ms
  useEffect(() => {
    let timer: ReturnType<typeof setInterval>;
    let prevEvCount = -1;

    const checkStatus = async () => {
      try {
        const res = await fetch("/api/v1/live/webcam/status");
        if (res.ok) {
          const data = await res.json();
          setRunning(data.running);
          setStatus(data.running ? data : { ...data, hand_in_basket: false, people_count: 0, people_in_zone: 0, people_in_zone_ids: [] });
          if (data.enable_basket !== undefined) {
            setBasketEnabled(data.enable_basket);
            try { localStorage.setItem("cctv_live_enable_basket", String(data.enable_basket)); } catch {}
          }
          if (data.enable_zone !== undefined) {
            setZoneEnabled(data.enable_zone);
            try { localStorage.setItem("cctv_live_enable_zone", String(data.enable_zone)); } catch {}
          }
          if (data.flip_h !== undefined) {
            setFlipH(data.flip_h);
            try { localStorage.setItem("cctv_live_flip_h", String(data.flip_h)); } catch {}
          }
          if (data.flip_v !== undefined) {
            setFlipV(data.flip_v);
            try { localStorage.setItem("cctv_live_flip_v", String(data.flip_v)); } catch {}
          }
          if (data.basket_roi && !showRoiPanel) {
            setBasketRoi(data.basket_roi);
            try { localStorage.setItem("cctv_live_basket_roi", JSON.stringify(data.basket_roi)); } catch {}
          }
          if (data.security_zone_roi && !showRoiPanel) {
            setZoneRoi(data.security_zone_roi);
            try { localStorage.setItem("cctv_live_zone_roi", JSON.stringify(data.security_zone_roi)); } catch {}
          }

          if (data.model_mode) setModelMode(data.model_mode);
          if (data.precision_mode) setPrecisionMode(data.precision_mode);
          if (data.enable_skeleton !== undefined) setEnableSkeleton(data.enable_skeleton);
          if (data.telegram) {
            setTelegramChatId("8269826134");
            if (!showTelegramModalRef.current && data.telegram.enabled !== undefined) {
              setTelegramEnabled(data.telegram.enabled);
            }
            if (!showTelegramModalRef.current && data.telegram.events) {
              setTelegramEvents(data.telegram.events);
            }
          }

          if (data.evidence_count !== undefined && data.evidence_count !== prevEvCount) {
            prevEvCount = data.evidence_count;
            fetchEvidence();
          }
        }
      } catch {
        // ignore
      }
    };

    checkStatus();
    fetchEvidence();
    timer = setInterval(checkStatus, 1000);
    return () => clearInterval(timer);
  }, [showRoiPanel]);

  // Frame polling loop fallback
  useEffect(() => {
    if (!running || (renderMode !== "polling" && !streamFailed)) return;

    let active = true;
    let pollTimer: ReturnType<typeof setTimeout>;

    const pollFrame = async () => {
      if (!active) return;
      try {
        const res = await fetch(`/api/v1/live/webcam/frame.jpg?t=${Date.now()}`);
        if (res.ok && res.status === 200) {
          const blob = await res.blob();
          if (active) {
            const url = URL.createObjectURL(blob);
            setFrameUrl((prev) => {
              if (prev) URL.revokeObjectURL(prev);
              return url;
            });
          }
        }
      } catch {
        // ignore
      }

      if (active) {
        pollTimer = setTimeout(pollFrame, 65);
      }
    };

    pollFrame();
    return () => {
      active = false;
      clearTimeout(pollTimer);
    };
  }, [running, renderMode, streamFailed]);

  // Update backend config
  const updateConfig = async (newCfg: {
    enable_basket?: boolean;
    enable_zone?: boolean;
    flip_h?: boolean;
    flip_v?: boolean;
    enable_video_simulation?: boolean;
    loop_video?: boolean;
    model_mode?: string;
    precision_mode?: string;
    enable_skeleton?: boolean;
    telegram_token?: string;
    telegram_chat_id?: string;
    telegram_enabled?: boolean;
    telegram_events?: string[];
  }) => {
    try {
      const res = await fetch("/api/v1/live/webcam/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newCfg),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.telegram?.enabled !== undefined) {
          setTelegramEnabled(data.telegram.enabled);
          try { localStorage.setItem("cctv_live_telegram_enabled", String(data.telegram.enabled)); } catch {}
        }
      }
    } catch {
      // ignore
    }
  };

  // Toggle skeleton display
  const toggleSkeleton = () => {
    const next = !enableSkeleton;
    setEnableSkeleton(next);
    try { localStorage.setItem("cctv_live_enable_skeleton", String(next)); } catch {}
    updateConfig({ enable_skeleton: next });
    showToast(next ? "Đã bật hiển thị Khung Xương (Skeleton)" : "Đã ẩn Khung Xương", "info");
  };

  // Telegram Bot Handlers (Locked to @diep_nguyenk5)
  const handleToggleTelegramEnabled = async (checked: boolean) => {
    setTelegramEnabled(checked);
    try {
      localStorage.setItem("cctv_live_telegram_enabled", String(checked));
    } catch {}
    await updateConfig({
      telegram_enabled: checked,
      telegram_chat_id: "8269826134",
      telegram_events: telegramEvents,
    });
    showToast(
      checked ? "Đã BẬT cảnh báo Telegram cho @diep_nguyenk5" : "Đã TẮT cảnh báo Telegram",
      checked ? "success" : "info"
    );
  };

  const handleToggleTelegramEvent = async (ev: string, checked: boolean) => {
    const nextEvents = checked
      ? [...telegramEvents, ev]
      : telegramEvents.filter((item) => item !== ev);
    setTelegramEvents(nextEvents);
    await updateConfig({
      telegram_enabled: telegramEnabled,
      telegram_chat_id: "8269826134",
      telegram_events: nextEvents,
    });
  };

  const handleTestTelegram = async () => {
    setTestingTelegram(true);
    try {
      const res = await fetch("/api/v1/live/webcam/telegram/test", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        showToast("Đã gửi tin nhắn thử nghiệm tới Telegram!", "success");
      } else {
        showToast(data.message || "Gửi tin nhắn thử thất bại!", "info");
      }
    } catch (err: any) {
      showToast("Lỗi khi gửi test: " + err.message, "info");
    } finally {
      setTestingTelegram(false);
    }
  };

  const handleSaveTelegramConfig = async (enabled: boolean, chatId: string, events: string[]) => {
    setTelegramEnabled(enabled);
    setTelegramChatId(chatId);
    setTelegramEvents(events);
    try {
      localStorage.setItem("cctv_live_telegram_enabled", String(enabled));
      localStorage.setItem("cctv_live_telegram_chat_id", chatId);
    } catch {}
    await updateConfig({
      telegram_enabled: enabled,
      telegram_chat_id: chatId,
      telegram_events: events,
    });
    showToast("Đã lưu cấu hình cảnh báo Telegram", "success");
    setShowTelegramModal(false);
  };

  const handleSaveAiConfig = async (mMode: "yolo_pose" | "yolo_detect", pMode: "cuda_fp16" | "onnx" | "cpu", skel: boolean) => {
    setModelMode(mMode);
    setPrecisionMode(pMode);
    setEnableSkeleton(skel);
    try {
      localStorage.setItem("cctv_live_model_mode", mMode);
      localStorage.setItem("cctv_live_precision_mode", pMode);
      localStorage.setItem("cctv_live_enable_skeleton", String(skel));
    } catch {}
    await updateConfig({
      model_mode: mMode,
      precision_mode: pMode,
      enable_skeleton: skel,
    });
    showToast(`Đã áp dụng: ${mMode === "yolo_pose" ? "YOLO11-Pose" : "YOLO Detect"} (${pMode.toUpperCase()})`, "success");
    setShowAiModal(false);
  };

  // Fetch Simulated Videos
  const fetchSimVideos = async () => {
    try {
      const res = await fetch("/api/v1/live/video/list");
      if (res.ok) {
        const data = await res.json();
        const items: SimVideoItem[] = data.items || [];
        setSimVideos(items);
        if (items.length > 0) {
          setSelectedSimVideo((prev) => {
            if (prev && items.some((it) => it.video_id === prev.video_id)) return prev;
            return items[0];
          });
        }
      }
    } catch {}
  };

  useEffect(() => {
    if (enableVideoSim) {
      fetchSimVideos();
    }
  }, [enableVideoSim]);

  // Feature Flag: Toggle Simulated Video Mode (with safe fallback)
  const toggleVideoSimFlag = () => {
    const next = !enableVideoSim;
    setEnableVideoSim(next);
    try {
      localStorage.setItem("cctv_live_enable_video_simulation", String(next));
    } catch {}
    updateConfig({ enable_video_simulation: next });
    if (!next && sourceType === "file") {
      setSourceType("webcam0");
    }
    showToast(
      next ? "Đã bật chế độ mô phỏng Video File" : "Đã chuyển về chế độ Webcam / RTSP chuẩn (Fallback)",
      "info"
    );
  };

  // Upload a new simulated video file
  const handleSimVideoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const file = files[0];
    setUploadingVideo(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/v1/live/video/upload", {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        const uploaded: SimVideoItem = await res.json();
        setSimVideos((prev) => [uploaded, ...prev.filter((v) => v.video_id !== uploaded.video_id)]);
        setSelectedSimVideo(uploaded);
        showToast(`Đã nạp video mô phỏng: ${uploaded.filename}`, "success");
      } else {
        const err = await res.json();
        showToast(err.detail || "Lỗi tải video", "info");
      }
    } catch {
      showToast("Không thể tải file video lên máy chủ", "info");
    } finally {
      setUploadingVideo(false);
      e.target.value = "";
    }
  };

  // Delete an uploaded simulated video file
  const handleDeleteSimVideo = async (videoId: string) => {
    try {
      const res = await fetch(`/api/v1/live/video/${videoId}`, { method: "DELETE" });
      if (res.ok) {
        setSimVideos((prev) => {
          const next = prev.filter((v) => v.video_id !== videoId);
          if (selectedSimVideo?.video_id === videoId) {
            setSelectedSimVideo(next[0] || null);
          }
          return next;
        });
        showToast("Đã xóa video mô phỏng", "info");
      }
    } catch {}
  };

  // Flip Handlers
  const toggleFlipH = () => {
    const next = !flipH;
    setFlipH(next);
    try { localStorage.setItem("cctv_live_flip_h", String(next)); } catch {}
    updateConfig({ flip_h: next });
    showToast(next ? "Đã bật lật gương ngang" : "Đã tắt lật gương", "info");
  };

  const toggleFlipV = () => {
    const next = !flipV;
    setFlipV(next);
    try { localStorage.setItem("cctv_live_flip_v", String(next)); } catch {}
    updateConfig({ flip_v: next });
    showToast(next ? "Đã bật lật ngược dọc" : "Đã tắt lật dọc", "info");
  };

  const toggleBasket = () => {
    const next = !basketEnabled;
    setBasketEnabled(next);
    try { localStorage.setItem("cctv_live_enable_basket", String(next)); } catch {}
    updateConfig({ enable_basket: next });
    showToast(next ? "Đã bật khung rổ tiền" : "Đã ẩn khung rổ tiền", "info");
  };

  const toggleZone = () => {
    const next = !zoneEnabled;
    setZoneEnabled(next);
    try { localStorage.setItem("cctv_live_enable_zone", String(next)); } catch {}
    updateConfig({ enable_zone: next });
    showToast(next ? "Đã bật khung quầy an ninh ngoài" : "Đã ẩn khung quầy an ninh ngoài", "info");
  };

  // Toggle Camera Start / Stop
  const toggleCamera = async () => {
    setLoading(true);
    setStreamFailed(false);
    try {
      if (running) {
        const res = await fetch("/api/v1/live/webcam/stop", { method: "POST" });
        if (res.ok) {
          setRunning(false);
          setStatus((prev) => ({ ...prev, hand_in_basket: false, people_count: 0, people_in_zone: 0, people_in_zone_ids: [] }));
          setFrameUrl(null);
          setShowRoiPanel(false);
          showToast("Đã ngắt kết nối camera", "info");
        }
      } else {
        let endpoint = "/api/v1/live/webcam/start";
        if (sourceType === "webcam0") endpoint += "?device_index=0";
        else if (sourceType === "webcam1") endpoint += "?device_index=1";
        else if (sourceType === "rtsp") endpoint += `?source=${encodeURIComponent(rtspUrl)}`;
        else if (sourceType === "file") {
          if (!selectedSimVideo) {
            showToast("Vui lòng tải lên hoặc chọn một file video để mô phỏng", "info");
            setLoading(false);
            return;
          }
          endpoint += `?source=${encodeURIComponent(selectedSimVideo.filepath)}&loop=${loopVideo}`;
        }

        const res = await fetch(endpoint, { method: "POST" });
        if (res.ok) {
          const data = await res.json();
          setRunning(data.running);
          setStreamKey(Date.now());
          updateConfig({ flip_h: flipH, flip_v: flipV, enable_basket: basketEnabled, enable_zone: zoneEnabled });
          showToast(
            sourceType === "file"
              ? `Đang chạy mô phỏng video: ${selectedSimVideo?.filename || ""}`
              : "Đã kích hoạt camera & AI giám sát rổ tiền",
            "success"
          );
        } else {
          showToast("Không thể kết nối nguồn camera đã chọn", "info");
        }
      }
    } catch (err) {
      console.error("Camera connection error:", err);
    } finally {
      setLoading(false);
    }
  };

  // Save Adjusted Basket ROI to Backend
  const saveBasketRoiToBackend = async (pointsToSave: Point[] = basketRoi) => {
    setRoiSaving(true);
    try {
      try { localStorage.setItem("cctv_live_basket_roi", JSON.stringify(pointsToSave)); } catch {}
      const res = await fetch("/api/v1/live/basket/roi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points: pointsToSave }),
      });
      if (res.ok) {
        showToast("Đã lưu kích thước rổ tiền mới", "success");
      }
    } catch (err) {
      console.error("Save Basket ROI error:", err);
    } finally {
      setRoiSaving(false);
    }
  };

  // Save Adjusted Security Zone ROI to Backend
  const saveZoneRoiToBackend = async (pointsToSave: Point[] = zoneRoi) => {
    setRoiSaving(true);
    try {
      try { localStorage.setItem("cctv_live_zone_roi", JSON.stringify(pointsToSave)); } catch {}
      const res = await fetch("/api/v1/live/zone/roi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points: pointsToSave }),
      });
      if (res.ok) {
        showToast("Đã lưu kích thước khung quầy an ninh ngoài", "success");
      }
    } catch (err) {
      console.error("Save Zone ROI error:", err);
    } finally {
      setRoiSaving(false);
    }
  };

  const applyPreset = (key: string) => {
    if (roiTarget === "basket") {
      const preset = PRESETS_BASKET[key];
      if (preset) {
        setBasketRoi(preset.points);
        saveBasketRoiToBackend(preset.points);
      }
    } else {
      // Scale outer zone around the selected basket preset
      const bPoints = PRESETS_BASKET[key]?.points || basketRoi;
      autoExpandZoneAroundBasket(bPoints);
    }
  };

  const autoExpandZoneAroundBasket = (basePoints: Point[] = basketRoi) => {
    const minX = Math.min(...basePoints.map((p) => p[0]));
    const maxX = Math.max(...basePoints.map((p) => p[0]));
    const minY = Math.min(...basePoints.map((p) => p[1]));
    const maxY = Math.max(...basePoints.map((p) => p[1]));
    const marginX = 55;
    const marginY = 50;
    const newZone: Point[] = [
      [Math.max(10, minX - marginX + 15), Math.max(10, minY - marginY)],
      [Math.min(630, maxX + marginX - 15), Math.max(10, minY - marginY)],
      [Math.min(630, maxX + marginX), Math.min(470, maxY + marginY)],
      [Math.max(10, minX - marginX), Math.min(470, maxY + marginY)],
    ];
    setZoneRoi(newZone);
    saveZoneRoiToBackend(newZone);
    showToast("Đã tự động mở rộng khung quầy bao quanh rổ tiền", "info");
  };

  // Active ROI points depending on active target
  const activeRoi = roiTarget === "basket" ? basketRoi : zoneRoi;
  const dims = getRoiDimensions(activeRoi);

  const updateDimensions = (newDims: Partial<{ cx: number; cy: number; w: number; h: number }>) => {
    const cur = getRoiDimensions(activeRoi);
    const cx = newDims.cx !== undefined ? newDims.cx : cur.cx;
    const cy = newDims.cy !== undefined ? newDims.cy : cur.cy;
    const w = newDims.w !== undefined ? newDims.w : cur.w;
    const h = newDims.h !== undefined ? newDims.h : cur.h;

    const hw = Math.round(w / 2);
    const hh = Math.round(h / 2);
    const perspectiveInward = Math.round(w * 0.08);

    const newPoints: Point[] = [
      [Math.max(10, cx - hw + perspectiveInward), Math.max(10, cy - hh)],
      [Math.min(630, cx + hw - perspectiveInward), Math.max(10, cy - hh)],
      [Math.min(630, cx + hw), Math.min(470, cy + hh)],
      [Math.max(10, cx - hw), Math.min(470, cy + hh)],
    ];

    if (roiTarget === "basket") {
      setBasketRoi(newPoints);
      saveBasketRoiToBackend(newPoints);
    } else {
      setZoneRoi(newPoints);
      saveZoneRoiToBackend(newPoints);
    }
  };

  // SVG Direct-Manipulation Drag Handlers
  const handlePointerDown = (index: number, e: React.PointerEvent) => {
    e.stopPropagation();
    setActiveHandleIndex(index);
    (e.target as Element).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (activeHandleIndex === null || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    let x = Math.round(((e.clientX - rect.left) / rect.width) * 640);
    let y = Math.round(((e.clientY - rect.top) / rect.height) * 480);
    x = Math.max(10, Math.min(630, x));
    y = Math.max(10, Math.min(470, y));

    if (roiTarget === "basket") {
      setBasketRoi((prev) => {
        const next = [...prev];
        next[activeHandleIndex] = [x, y];
        return next;
      });
    } else {
      setZoneRoi((prev) => {
        const next = [...prev];
        next[activeHandleIndex] = [x, y];
        return next;
      });
    }
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    if (activeHandleIndex !== null) {
      setActiveHandleIndex(null);
      (e.target as Element).releasePointerCapture(e.pointerId);
      if (roiTarget === "basket") {
        saveBasketRoiToBackend();
      } else {
        saveZoneRoiToBackend();
      }
    }
  };

  // Manual Snapshot Capture
  const handleManualCapture = async () => {
    if (!running) return;
    setCapturingSnapshot(true);
    try {
      const res = await fetch("/api/v1/live/evidence/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event_type: "CHỤP THỦ CÔNG",
          summary: "Người dùng chụp bằng chứng thủ công từ bảng điều khiển",
        }),
      });
      if (res.ok) {
        const item = await res.json();
        showToast(`Đã lưu bằng chứng #${item.id.slice(-6)}`, "success");
        fetchEvidence();
      }
    } catch (err) {
      console.error("Capture snapshot error:", err);
    } finally {
      setCapturingSnapshot(false);
    }
  };

  // Clear Evidence
  const handleClearEvidence = () => {
    askConfirm({
      title: "Xóa toàn bộ bằng chứng?",
      message: "Bạn có chắc muốn xóa tất cả ảnh chụp bằng chứng giao dịch rổ tiền khỏi hệ thống?",
      confirmText: "Xóa tất cả",
      kind: "danger",
      onConfirm: async () => {
        try {
          const res = await fetch("/api/v1/live/evidence", { method: "DELETE" });
          if (res.ok) {
            setEvidenceList([]);
            setSelectedEvidence(null);
            showToast("Đã làm sạch nhật ký bằng chứng", "info");
          }
        } catch {
          // ignore
        }
      },
    });
  };

  // Fullscreen
  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen?.().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  const filteredEvidence = evidenceList.filter((item) => {
    if (selectedFilter === "ALL") return true;
    return item.event_type.toUpperCase().includes(selectedFilter);
  });

  const isAlert = Boolean(running && basketEnabled && status.hand_in_basket);

  const getImageStyle = (): React.CSSProperties => {
    if (!isFocusedZoom) {
      return {
        transform: "none",
        transformOrigin: "center center",
      };
    }
    if (!basketRoi || basketRoi.length === 0) {
      return {
        transformOrigin: "27.7% 57.8%",
        transform: "translate(22.3%, -7.8%) scale(2.3)",
      };
    }
    const avgX = basketRoi.reduce((acc, p) => acc + p[0], 0) / basketRoi.length;
    const avgY = basketRoi.reduce((acc, p) => acc + p[1], 0) / basketRoi.length;
    const xPct = (avgX / 640) * 100;
    const yPct = (avgY / 480) * 100;
    const moveX = 50 - xPct;
    const moveY = 50 - yPct;

    return {
      transformOrigin: `${xPct.toFixed(2)}% ${yPct.toFixed(2)}%`,
      transform: `translate(${moveX.toFixed(2)}%, ${moveY.toFixed(2)}%) scale(2.3)`,
    };
  };

  const basketPointsString = basketRoi.map((p) => p.join(",")).join(" ");
  const zonePointsString = zoneRoi.map((p) => p.join(",")).join(" ");

  return (
    <div className={`cctv-dashboard ${isFullscreen ? "is-fullscreen" : ""}`} ref={containerRef}>
      {/* Top Source & Connection Bar */}
      <div className="terminal-bar">
        <div className="terminal-left">
          <label className="terminal-field">
            <span className="terminal-label">Nguồn Camera:</span>
            <select
              value={sourceType}
              disabled={running || loading}
              onChange={(e) => {
                const val = e.target.value as "webcam0" | "webcam1" | "rtsp" | "file";
                setSourceType(val);
                if (val === "rtsp") setShowRtspInput(true);
              }}
              className="terminal-select"
            >
              <option value="webcam0">Webcam Laptop (Tích hợp #0)</option>
              <option value="webcam1">Camera USB Ngoài (#1)</option>
              <option value="rtsp">Luồng RTSP Đầu Ghi CCTV</option>
              {enableVideoSim && <option value="file">🎬 Mô Phỏng Video File (Kiểm thử offline)</option>}
            </select>
          </label>

          {/* Feature Flag Toggle Button: On / Off (Fallback) */}
          <button
            type="button"
            className={`btn-flag-toggle ${enableVideoSim ? "is-active" : ""}`}
            onClick={toggleVideoSimFlag}
            disabled={running}
            title={enableVideoSim ? "Tắt tính năng mô phỏng video file (trở lại camera thực)" : "Bật tính năng mô phỏng video file"}
          >
            <span className="flag-icon">🎬</span>
            <span className="flag-label">Mô phỏng Video:</span>
            <span className={`deck-pill ${enableVideoSim ? "pill-active" : "pill-muted"}`}>
              {enableVideoSim ? "BẬT" : "TẮT"}
            </span>
          </button>

          {sourceType === "rtsp" && (
            <button
              type="button"
              className="btn-subtle"
              disabled={running}
              onClick={() => setShowRtspInput(!showRtspInput)}
            >
              {showRtspInput ? "Đóng URL" : "Cấu hình RTSP"}
            </button>
          )}
        </div>

        <div className="terminal-right">
          <div className="terminal-meta">
            {running && (
              <>
                <span className="badge-live">LIVE</span>
                <span className="meta-pill">{status.fps || 0} FPS</span>
                <span className="meta-pill">{status.inference_ms || 0} ms</span>
                <span className="meta-pill gpu">{status.device_name || "GPU"}</span>
              </>
            )}
          </div>

          <button
            type="button"
            className={running ? "btn-disconnect" : "btn-connect"}
            onClick={toggleCamera}
            disabled={loading}
          >
            {loading ? "Đang xử lý..." : running ? "Ngắt Kết Nối" : sourceType === "file" ? "Chạy Mô Phỏng Video" : "Bật Camera"}
          </button>
        </div>
      </div>

      {/* RTSP URL Drawer */}
      {sourceType === "rtsp" && (showRtspInput || !running) && (
        <div className="rtsp-drawer">
          <input
            type="text"
            className="terminal-input"
            value={rtspUrl}
            disabled={running}
            onChange={(e) => setRtspUrl(e.target.value)}
            placeholder="rtsp://admin:password@192.168.1.100:554/stream"
          />
          <span className="rtsp-caption">Hỗ trợ luồng RTSP H.264/H.265 từ đầu ghi Dahua, Hikvision, KBVision hoặc camera IP nội bộ</span>
        </div>
      )}

      {/* Simulated Video Drawer */}
      {enableVideoSim && sourceType === "file" && (
        <div className="sim-video-drawer">
          <div className="sim-drawer-header">
            <div className="sim-drawer-title">
              <span className="sim-icon">🎬</span>
              <div>
                <strong>Mô phỏng Luồng CCTV từ Tệp Video</strong>
                <p className="sim-subtitle">Chạy toàn bộ AI logic (YOLO, vùng an ninh, rổ tiền, bắt bàn tay, cảnh báo) như camera thực với tốc độ tự nhiên.</p>
              </div>
            </div>
            <div className="sim-loop-toggle">
              <label className="deck-toggle">
                <input
                  type="checkbox"
                  checked={loopVideo}
                  disabled={running}
                  onChange={(e) => setLoopVideo(e.target.checked)}
                />
                <span className="deck-toggle-slider" />
              </label>
              <span className="sim-loop-text">Lặp vô tận video</span>
            </div>
          </div>

          <div className="sim-drawer-content">
            <div className="sim-video-select-area">
              <label className="sim-field-label">Chọn video mô phỏng:</label>
              <div className="sim-select-row">
                <select
                  className="terminal-select sim-select"
                  value={selectedSimVideo?.video_id || ""}
                  disabled={running || simVideos.length === 0}
                  onChange={(e) => {
                    const found = simVideos.find((v) => v.video_id === e.target.value);
                    if (found) setSelectedSimVideo(found);
                  }}
                >
                  {simVideos.length === 0 ? (
                    <option value="">(Chưa có video nào - Vui lòng tải lên)</option>
                  ) : (
                    simVideos.map((v) => (
                      <option key={v.video_id} value={v.video_id}>
                        {v.filename} ({v.duration_sec.toFixed(1)}s • {v.fps.toFixed(0)} FPS • {v.width}x{v.height})
                      </option>
                    ))
                  )}
                </select>

                {selectedSimVideo && !running && (
                  <button
                    type="button"
                    className="btn-danger-outline btn-delete-sim"
                    onClick={() => handleDeleteSimVideo(selectedSimVideo.video_id)}
                    title="Xóa video này"
                  >
                    🗑️ Xóa
                  </button>
                )}
              </div>

              {selectedSimVideo && (
                <div className="sim-meta-chips">
                  <span className="sim-meta-chip">⏱️ {selectedSimVideo.duration_sec.toFixed(1)}s</span>
                  <span className="sim-meta-chip">🎞️ {selectedSimVideo.fps.toFixed(0)} FPS</span>
                  <span className="sim-meta-chip">📐 {selectedSimVideo.width}×{selectedSimVideo.height}</span>
                  <span className="sim-meta-chip">📦 {(selectedSimVideo.size_bytes / (1024 * 1024)).toFixed(1)} MB</span>
                </div>
              )}
            </div>

            <div className="sim-upload-area">
              <label className="sim-field-label">Hoặc tải lên video kiểm thử mới:</label>
              <label className={`sim-upload-box ${uploadingVideo ? "uploading" : ""}`}>
                <input
                  type="file"
                  accept="video/mp4,video/avi,video/mov,video/mkv,video/webm"
                  disabled={running || uploadingVideo}
                  onChange={handleSimVideoUpload}
                  style={{ display: "none" }}
                />
                <span className="sim-upload-icon">{uploadingVideo ? "⏳" : "📤"}</span>
                <span className="sim-upload-text">
                  {uploadingVideo ? "Đang tải lên & kiểm tra video..." : "Chọn tệp MP4, AVI, MOV, MKV để tải lên"}
                </span>
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Main Video Viewport */}
      <div className={`cctv-viewport-frame ${isAlert ? "alert-border" : ""}`}>
        {/* HUD Overlay Bar */}
        <div className="hud-overlay">
          <div className="hud-left">
            <span className="hud-title">Giám sát Quầy & Rổ Tiền</span>
            {running && (
              <span className="hud-sub">
                {status.people_count || 0} người trong camera
                {status.people_in_zone_ids && status.people_in_zone_ids.length > 0 ? (
                  <strong className="hud-counter-customer">
                    {" "}· Khách tại quầy: Người #{status.people_in_zone_ids.join(", #")}
                  </strong>
                ) : (
                  ` · ${status.people_in_zone || 0} tại quầy`
                )}
              </span>
            )}
          </div>

          <div className="hud-right" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {running && status.detected_currency && (
              <div className="hud-pill" style={{ background: "#064E3B", color: "#34D399", border: "1px solid #059669" }}>
                💵 {status.detected_currency}
              </div>
            )}
            {running && status.is_pinching && (
              <div className="hud-pill alert" style={{ animation: "pulse 1s infinite" }}>
                🤏 NHÓN TIỀN (PINCH)
              </div>
            )}
            {running && status.hand_gesture && status.hand_gesture !== "None" && (
              <div className="hud-pill" style={{ background: "#1E293B", color: "#38BDF8", border: "1px solid #0284C7" }}>
                {status.hand_gesture === "Closed_Fist" ? "✊ Nắm tay" : status.hand_gesture === "Open_Palm" ? "🖐️ Xòe tay" : `✋ ${status.hand_gesture}`}
              </div>
            )}
            {isAlert ? (
              <div className="hud-pill alert">
                <span className="pulsing-dot" />
                CẢNH BÁO: TAY TRONG RỔ TIỀN
              </div>
            ) : (
              <div className={`hud-pill ${running ? "normal" : "dimmed"}`}>
                <span className={`status-dot ${running ? "green" : "gray"}`} />
                {running ? "Rổ tiền: Bình thường" : "Chưa sẵn sàng"}
              </div>
            )}
          </div>
        </div>

        {/* Video Surface */}
        <div className="cctv-video-container">
          {running ? (
            <div className="video-viewport-wrapper" style={getImageStyle()}>
              {!streamFailed && renderMode === "stream" ? (
                <img
                  key={streamKey}
                  src={`/api/v1/live/webcam/stream?t=${streamKey}`}
                  alt="CCTV Live Stream"
                  className="cctv-raw-img"
                  onError={() => {
                    console.warn("Stream error, falling back to polling");
                    setStreamFailed(true);
                  }}
                />
              ) : frameUrl ? (
                <img src={frameUrl} alt="CCTV Polling Frame" className="cctv-raw-img" />
              ) : (
                <div className="viewport-placeholder">Đang nhận dữ liệu khung hình...</div>
              )}

              {/* Direct-Manipulation SVG ROI Drag & Resize Overlay (100% pixel-aligned over image) */}
              {showRoiPanel && running && (
                <svg
                  ref={svgRef}
                  viewBox="0 0 640 480"
                  preserveAspectRatio="none"
                  className="roi-editor-svg"
                  onPointerMove={handlePointerMove}
                  onPointerUp={handlePointerUp}
                >
                  {/* Inactive Zone (Background Reference) */}
                  {roiTarget === "basket" && zoneEnabled && (
                    <polygon
                      points={zonePointsString}
                      className="roi-polygon-reference-zone"
                    />
                  )}
                  {roiTarget === "security_zone" && basketEnabled && (
                    <polygon
                      points={basketPointsString}
                      className="roi-polygon-reference-basket"
                    />
                  )}

                  {/* Active Target Polygon */}
                  <polygon
                    points={roiTarget === "basket" ? basketPointsString : zonePointsString}
                    className={roiTarget === "basket" ? "roi-polygon-interactive" : "roi-polygon-interactive-zone"}
                  />

                  {/* 4 Corner Drag Handles of Active Target */}
                  {activeRoi.map((point, idx) => (
                    <g key={idx} className="roi-handle-group">
                      <circle
                        cx={point[0]}
                        cy={point[1]}
                        r={12}
                        className={`roi-handle-circle ${roiTarget === "security_zone" ? "zone-circle" : ""} ${activeHandleIndex === idx ? "is-dragging" : ""}`}
                        onPointerDown={(e) => handlePointerDown(idx, e)}
                      />
                      <text
                        x={point[0]}
                        y={point[1] - 16}
                        textAnchor="middle"
                        className="roi-handle-text"
                      >
                        {roiTarget === "basket" ? "Rổ #" : "Quầy #"}{idx + 1} ({point[0]}, {point[1]})
                      </text>
                    </g>
                  ))}
                </svg>
              )}
            </div>
          ) : (
            <div className="viewport-placeholder">
              <div className="placeholder-content">
                <span className="placeholder-badge">CAMERA CHƯA KÍCH HOẠT</span>
                <h3>Giám Sát Rổ Tiền & Nhân Viên POS</h3>
                <p>Nhấn <strong>Bật Camera</strong> phía trên để hệ thống tự động phát hiện người, theo dõi rổ tiền và ghi lại bằng chứng khi có giao dịch.</p>
              </div>
            </div>
          )}
        </div>

        {/* Dedicated Cash Basket & Security Zone Geometry Deck */}
        {showRoiPanel && running && (
          <div className="roi-geometry-deck">
            <div className="deck-header">
              <div className="deck-title-box">
                {/* Target Selector: Switch between Basket ROI and Outer Counter Zone */}
                <div className="deck-target-selector">
                  <button
                    type="button"
                    className={`target-tab-btn ${roiTarget === "basket" ? "is-active" : ""}`}
                    onClick={() => {
                      setRoiTarget("basket");
                      setActiveHandleIndex(null);
                    }}
                  >
                    Rổ Tiền (Nội Bộ)
                  </button>
                  <button
                    type="button"
                    className={`target-tab-btn zone ${roiTarget === "security_zone" ? "is-active" : ""}`}
                    onClick={() => {
                      setRoiTarget("security_zone");
                      setActiveHandleIndex(null);
                    }}
                  >
                    Khung Quầy (Khung Ngoài)
                  </button>
                </div>
                <span className="deck-target-desc">
                  {roiTarget === "basket"
                    ? "Khu vực khay rổ tiền: AI phát hiện tay thò vào / rút tiền."
                    : "Khu vực quầy thu ngân: AI nhận diện nhân viên/khách hàng đứng tại quầy."}
                </span>
              </div>

              <div className="deck-presets">
                {roiTarget === "basket" ? (
                  Object.entries(PRESETS_BASKET).map(([key, preset]) => (
                    <button
                      key={key}
                      type="button"
                      className="deck-preset-btn"
                      onClick={() => applyPreset(key)}
                    >
                      {preset.label}
                    </button>
                  ))
                ) : (
                  <button
                    type="button"
                    className="deck-preset-btn highlight"
                    onClick={() => autoExpandZoneAroundBasket()}
                    title="Tự động căn chỉnh khung quầy ngoài bao quanh rổ tiền"
                  >
                    Tự Động Mở Rộng Quanh Rổ
                  </button>
                )}
              </div>
            </div>

            <div className="deck-sliders-grid">
              <div className="deck-slider-item">
                <div className="slider-label-row">
                  <span>{roiTarget === "basket" ? "Chiều Rộng Rổ Tiền" : "Chiều Rộng Khung Quầy"}</span>
                  <strong>{dims.w} px</strong>
                </div>
                <input
                  type="range"
                  min="120"
                  max="600"
                  step="5"
                  value={dims.w}
                  onChange={(e) => updateDimensions({ w: Number(e.target.value) })}
                  className={`deck-range ${roiTarget === "security_zone" ? "cyan" : ""}`}
                />
              </div>

              <div className="deck-slider-item">
                <div className="slider-label-row">
                  <span>{roiTarget === "basket" ? "Chiều Cao Rổ Tiền" : "Chiều Cao Khung Quầy"}</span>
                  <strong>{dims.h} px</strong>
                </div>
                <input
                  type="range"
                  min="80"
                  max="440"
                  step="5"
                  value={dims.h}
                  onChange={(e) => updateDimensions({ h: Number(e.target.value) })}
                  className={`deck-range ${roiTarget === "security_zone" ? "cyan" : ""}`}
                />
              </div>

              <div className="deck-slider-item">
                <div className="slider-label-row">
                  <span>Vị Trí Ngang (Trục X)</span>
                  <strong>{dims.cx} px</strong>
                </div>
                <input
                  type="range"
                  min="60"
                  max="580"
                  step="5"
                  value={dims.cx}
                  onChange={(e) => updateDimensions({ cx: Number(e.target.value) })}
                  className={`deck-range ${roiTarget === "security_zone" ? "cyan" : ""}`}
                />
              </div>

              <div className="deck-slider-item">
                <div className="slider-label-row">
                  <span>Vị Trí Dọc (Trục Y)</span>
                  <strong>{dims.cy} px</strong>
                </div>
                <input
                  type="range"
                  min="60"
                  max="430"
                  step="5"
                  value={dims.cy}
                  onChange={(e) => updateDimensions({ cy: Number(e.target.value) })}
                  className={`deck-range ${roiTarget === "security_zone" ? "cyan" : ""}`}
                />
              </div>
            </div>

            <div className="deck-footer">
              <span className="deck-hint">
                Kéo 4 góc trực tiếp trên video hoặc di chuyển thanh trượt. Bạn có thể chuyển tab phía trên để chỉnh rổ hoặc khung ngoài.
              </span>
              <button
                type="button"
                className="btn-save-deck"
                onClick={() => {
                  if (roiTarget === "basket") saveBasketRoiToBackend();
                  else saveZoneRoiToBackend();
                  setShowRoiPanel(false);
                }}
                disabled={roiSaving}
              >
                {roiSaving ? "Đang lưu..." : "Lưu & Đóng Bảng Điều Chỉnh"}
              </button>
            </div>
          </div>
        )}

        {/* Apple Studio Control Deck */}
        <div className="cctv-control-deck">
          {/* Group 1: Góc Nhìn & Hiển Thị Camera */}
          <div className="deck-section">
            <span className="deck-section-title">GÓC NHÌN & CAMERA</span>
            <div className="deck-btn-group">
              <button
                type="button"
                className={`deck-btn ${flipH ? "is-active" : ""}`}
                onClick={toggleFlipH}
                title="Lật gương ngang (Khuyên dùng cho webcam)"
              >
                <span className="deck-btn-icon">⇄</span>
                <span className="deck-btn-label">Lật gương</span>
                <span className={`deck-pill ${flipH ? "pill-active" : "pill-muted"}`}>
                  {flipH ? "BẬT" : "TẮT"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn ${flipV ? "is-active" : ""}`}
                onClick={toggleFlipV}
                title="Lật ngược dọc (Khuyên dùng khi treo trần)"
              >
                <span className="deck-btn-icon">⇅</span>
                <span className="deck-btn-label">Lật dọc</span>
                <span className={`deck-pill ${flipV ? "pill-active" : "pill-muted"}`}>
                  {flipV ? "BẬT" : "TẮT"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn ${isFocusedZoom ? "is-active highlight" : ""}`}
                onClick={() => {
                  const next = !isFocusedZoom;
                  setIsFocusedZoom(next);
                  showToast(next ? "Đã bật Cận Cảnh Rổ Tiền (Zoom 2.3x vào rổ)" : "Đã tắt Cận Cảnh Rổ Tiền", "info");
                }}
                disabled={!running}
                title="Cận cảnh rổ tiền (Phóng to 2.3x căn chuẩn tâm rổ)"
              >
                <span className="deck-btn-icon">🔍</span>
                <span className="deck-btn-label">Cận cảnh rổ</span>
                <span className={`deck-pill ${isFocusedZoom ? "pill-blue" : "pill-muted"}`}>
                  {isFocusedZoom ? "2.3x (BẬT)" : "2.3x"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn ${isFullscreen ? "is-active" : ""}`}
                onClick={toggleFullscreen}
                title={isFullscreen ? "Thoát toàn màn hình" : "Xem toàn màn hình"}
              >
                <span className="deck-btn-icon">{isFullscreen ? "✕" : "⛶"}</span>
                <span className="deck-btn-label">
                  {isFullscreen ? "Thoát Toàn Màn Hình" : "Toàn Màn Hình"}
                </span>
              </button>
            </div>
          </div>

          <div className="deck-separator" />

          {/* Group 2: Vùng Giám Sát AI */}
          <div className="deck-section">
            <span className="deck-section-title">VÙNG GIÁM SÁT AI</span>
            <div className="deck-btn-group">
              <button
                type="button"
                className={`deck-btn ${basketEnabled ? "is-active" : ""}`}
                onClick={toggleBasket}
                disabled={!running}
                title="Bật/Tắt hiển thị và giám sát rổ tiền"
              >
                <span className="deck-btn-icon">🧺</span>
                <span className="deck-btn-label">Khung rổ tiền</span>
                <span className={`deck-pill ${basketEnabled ? "pill-blue" : "pill-muted"}`}>
                  {basketEnabled ? "BẬT" : "TẮT"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn ${zoneEnabled ? "is-active" : ""}`}
                onClick={toggleZone}
                disabled={!running}
                title="Bật/Tắt khung an ninh quầy thu ngân ngoài"
              >
                <span className="deck-btn-icon">🛡️</span>
                <span className="deck-btn-label">Khung ngoài quầy</span>
                <span className={`deck-pill ${zoneEnabled ? "pill-cyan" : "pill-muted"}`}>
                  {zoneEnabled ? "BẬT" : "TẮT"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn deck-btn-calibrate ${showRoiPanel ? "is-active highlight" : ""}`}
                onClick={() => setShowRoiPanel(!showRoiPanel)}
                disabled={!running}
                title="Mở bảng điều chỉnh kích thước rổ tiền và khung ngoài"
              >
                <span className="deck-btn-icon">📐</span>
                <span className="deck-btn-label">
                  {showRoiPanel ? "Đóng Chỉnh Khung" : "Chỉnh Kích Thước Rổ & Khung"}
                </span>
              </button>
            </div>
          </div>

          <div className="deck-separator" />

          {/* Group 3: Mô Hình AI & Tăng Tốc */}
          <div className="deck-section">
            <span className="deck-section-title">AI & GIA TỐC</span>
            <div className="deck-btn-group">
              <button
                type="button"
                className={`deck-btn ${modelMode === "yolo_pose" ? "is-active highlight" : ""}`}
                onClick={() => setShowAiModal(true)}
                title="Cấu hình mô hình AI (YOLO11-Pose / Detect) và chế độ tăng tốc FP16"
              >
                <span className="deck-btn-icon">⚡</span>
                <span className="deck-btn-label">
                  {modelMode === "yolo_pose" ? "YOLO11-Pose" : "YOLO Detect"}
                </span>
                <span className="deck-pill pill-cyan">
                  {precisionMode === "cuda_fp16" ? "CUDA FP16" : precisionMode === "onnx" ? "ONNX" : "CPU"}
                </span>
              </button>

              <button
                type="button"
                className={`deck-btn ${enableSkeleton ? "is-active" : ""}`}
                onClick={toggleSkeleton}
                disabled={!running}
                title="Bật/Tắt hiển thị khung xương và vị trí cổ tay"
              >
                <span className="deck-btn-icon">🦴</span>
                <span className="deck-btn-label">Khung Xương</span>
                <span className={`deck-pill ${enableSkeleton ? "pill-emerald" : "pill-muted"}`}>
                  {enableSkeleton ? "BẬT" : "TẮT"}
                </span>
              </button>
            </div>
          </div>

          <div className="deck-separator" />

          {/* Group 4: Telegram Bot Alert */}
          <div className="deck-section">
            <span className="deck-section-title">TELEGRAM BOT</span>
            <div className="deck-btn-group">
              <button
                type="button"
                className={`deck-btn ${telegramEnabled ? "is-active highlight" : ""}`}
                onClick={() => setShowTelegramModal(true)}
                title="Khóa cảnh báo độc quyền: @diep_nguyenk5 (8269826134)"
              >
                <span className="deck-btn-icon">✈️</span>
                <span className="deck-btn-label">@diep_nguyenk5</span>
                <span className={`deck-pill ${telegramEnabled ? "pill-emerald" : "pill-muted"}`}>
                  {telegramEnabled ? "8269826134" : "TẮT"}
                </span>
              </button>
            </div>
          </div>

          <div className="deck-separator" />

          {/* Group 5: Thao Tác Bằng Chứng Nhanh */}
          <div className="deck-section deck-section-action">
            <span className="deck-section-title">BẰNG CHỨNG</span>
            <div className="deck-btn-group">
              <button
                type="button"
                className="deck-btn-capture"
                onClick={handleManualCapture}
                disabled={!running || capturingSnapshot}
                title="Chụp ảnh bằng chứng ngay lập tức"
              >
                <span className="deck-btn-icon">
                  {capturingSnapshot ? "⏳" : "📸"}
                </span>
                <span className="deck-btn-label">
                  {capturingSnapshot ? "Đang Chụp..." : "Chụp Bằng Chứng"}
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Apple-style Floating Frosted Toast */}
      {toastMessage && (
        <div className={`apple-toast ${toastMessage.type}`}>
          <span className={`toast-dot ${toastMessage.type}`} />
          <span className="toast-text">{toastMessage.text}</span>
        </div>
      )}

      {/* Evidence Log & Strip Section */}
      <div className="evidence-section">
        <div className="evidence-header">
          <div className="evidence-title-group">
            <h3 className="evidence-title">Nhật Ký Bằng Chứng Giao Dịch</h3>
            <span className="evidence-count">{evidenceList.length} sự kiện</span>
          </div>

          <div className="evidence-filters">
            {["ALL", "RÚT TIỀN", "BỎ TIỀN", "CHẠM RỔ", "THỦ CÔNG"].map((f) => (
              <button
                key={f}
                type="button"
                className={`filter-btn ${selectedFilter === f ? "is-active" : ""}`}
                onClick={() => setSelectedFilter(f)}
              >
                {f === "ALL" ? "Tất cả" : f}
              </button>
            ))}

            {evidenceList.length > 0 && (
              <button
                type="button"
                className="btn-clear-evidence"
                onClick={handleClearEvidence}
              >
                Xóa tất cả
              </button>
            )}
          </div>
        </div>

        {filteredEvidence.length === 0 ? (
          <div className="evidence-empty">
            <span className="empty-label">CHƯA CÓ BẰNG CHỨNG</span>
            <p>Hệ thống sẽ tự động chụp và lưu vết khi nhân viên chạm tay vào rổ tiền hoặc thực hiện rút/bỏ tiền.</p>
          </div>
        ) : (
          <div className="evidence-grid">
            {filteredEvidence.map((item) => (
              <div
                key={item.id}
                className="evidence-card"
                onClick={() => setSelectedEvidence(item)}
              >
                <div className="evidence-thumb-box">
                  <img src={item.image_url} alt={item.event_type} className="evidence-thumb" loading="lazy" />
                  <span className={`evidence-badge ${item.event_type.replace(/\s+/g, "-").toLowerCase()}`}>
                    {item.event_type}
                  </span>
                </div>
                <div className="evidence-info">
                  <span className="evidence-time">
                    {new Date(item.timestamp).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </span>
                  <p className="evidence-summary" title={item.summary}>{item.summary}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* AI Model & Hardware Acceleration Modal */}
      {showAiModal && (
        <div className="modal-backdrop" onClick={() => setShowAiModal(false)}>
          <div className="settings-modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h4>Cấu Hình Mô Hình AI & Tăng Tốc Phần Cứng</h4>
                <span className="modal-time">Tối ưu hóa nhận diện thao tác và tốc độ khung hình (FPS)</span>
              </div>
              <button type="button" className="btn-close" onClick={() => setShowAiModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <div className="settings-section">
                <span className="settings-section-title">1. Chọn Mô Hình Nhận Diện (AI Model)</span>
                <div className="radio-cards-grid">
                  <label className={`radio-card ${modelMode === "yolo_pose" ? "is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="modelMode"
                      className="radio-card-input"
                      checked={modelMode === "yolo_pose"}
                      onChange={() => setModelMode("yolo_pose")}
                    />
                    <div className="radio-card-content">
                      <span className="radio-card-title">YOLO11-Pose (Khuyên dùng)</span>
                      <span className="radio-card-desc">
                        Bắt chính xác tọa độ 2 cổ tay để nhận diện thò tay rổ tiền. Không bị ảnh hưởng bởi găng tay, ánh sáng hay màu sắc.
                      </span>
                    </div>
                  </label>

                  <label className={`radio-card ${modelMode === "yolo_detect" ? "is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="modelMode"
                      className="radio-card-input"
                      checked={modelMode === "yolo_detect"}
                      onChange={() => setModelMode("yolo_detect")}
                    />
                    <div className="radio-card-content">
                      <span className="radio-card-title">YOLO Tiêu Chuẩn (Box Detect)</span>
                      <span className="radio-card-desc">
                        Nhận diện hộp người tiêu chuẩn kết hợp thuật toán phân tích màu da và chuyển động quang học.
                      </span>
                    </div>
                  </label>
                </div>
              </div>

              <div className="settings-section">
                <span className="settings-section-title">2. Chế Độ Gia Tốc (Hardware Acceleration)</span>
                <div className="radio-cards-grid">
                  <label className={`radio-card ${precisionMode === "cuda_fp16" ? "is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="precisionMode"
                      className="radio-card-input"
                      checked={precisionMode === "cuda_fp16"}
                      onChange={() => setPrecisionMode("cuda_fp16")}
                    />
                    <div className="radio-card-content">
                      <span className="radio-card-title">CUDA FP16 (RTX 3080 Ti)</span>
                      <span className="radio-card-desc">
                        Chạy GPU trực tiếp với độ chính xác bán thực (FP16). Đạt ~31ms/frame, 60+ FPS cực mượt mà.
                      </span>
                    </div>
                  </label>

                  <label className={`radio-card ${precisionMode === "onnx" ? "is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="precisionMode"
                      className="radio-card-input"
                      checked={precisionMode === "onnx"}
                      onChange={() => setPrecisionMode("onnx")}
                    />
                    <div className="radio-card-content">
                      <span className="radio-card-title">ONNX Runtime FP16</span>
                      <span className="radio-card-desc">
                        Chạy file yolo11n-pose.onnx tối ưu hóa với ONNX Runtime.
                      </span>
                    </div>
                  </label>

                  <label className={`radio-card ${precisionMode === "cpu" ? "is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="precisionMode"
                      className="radio-card-input"
                      checked={precisionMode === "cpu"}
                      onChange={() => setPrecisionMode("cpu")}
                    />
                    <div className="radio-card-content">
                      <span className="radio-card-title">CPU Chuẩn</span>
                      <span className="radio-card-desc">
                        Chạy chế độ tương thích hoàn toàn trên vi xử lý CPU (dành cho máy không có card rời).
                      </span>
                    </div>
                  </label>
                </div>
              </div>

              <div className="settings-section">
                <span className="settings-section-title">3. Kiến Trúc AI Đa Tầng Tích Hợp (Active Multi-Stage Pipeline)</span>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "8px" }}>
                  <div style={{ background: "#0F172A", border: "1px solid #1E293B", borderRadius: "8px", padding: "10px 12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <strong style={{ color: "#38BDF8", fontSize: "0.85rem" }}>🤖 Google MediaPipe Hands (21 Khớp 3D)</strong>
                      <p style={{ margin: "2px 0 0", color: "#94A3B8", fontSize: "0.75rem" }}>Phân tích cử chỉ ngón tay, đo khoảng cách nhón tiền (Pinch) và nắm tay (Fist) siêu tốc ~25ms.</p>
                    </div>
                    <span style={{ background: "#064E3B", color: "#34D399", padding: "2px 8px", borderRadius: "4px", fontSize: "0.7rem", fontWeight: 600 }}>TỰ ĐỘNG BẬT</span>
                  </div>

                  <div style={{ background: "#0F172A", border: "1px solid #1E293B", borderRadius: "8px", padding: "10px 12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <strong style={{ color: "#34D399", fontSize: "0.85rem" }}>💵 Nhận Diện Tiền Việt Nam (VND Currency YOLO)</strong>
                      <p style={{ margin: "2px 0 0", color: "#94A3B8", fontSize: "0.75rem" }}>Tự động phát hiện các mệnh giá Polymer: 10k, 20k, 50k, 100k, 200k, 500k VNĐ trong rổ.</p>
                    </div>
                    <span style={{ background: "#064E3B", color: "#34D399", padding: "2px 8px", borderRadius: "4px", fontSize: "0.7rem", fontWeight: 600 }}>SẴN SÀNG</span>
                  </div>

                  <div style={{ background: "#0F172A", border: "1px solid #1E293B", borderRadius: "8px", padding: "10px 12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <strong style={{ color: "#F59E0B", fontSize: "0.85rem" }}>🛡️ ATM & Cashier Shoplifting Monitor</strong>
                      <p style={{ margin: "2px 0 0", color: "#94A3B8", fontSize: "0.75rem" }}>Theo dõi hành vi che mặt, tiếp cận khả nghi tại khu vực quầy thu ngân.</p>
                    </div>
                    <span style={{ background: "#064E3B", color: "#34D399", padding: "2px 8px", borderRadius: "4px", fontSize: "0.7rem", fontWeight: 600 }}>SẴN SÀNG</span>
                  </div>
                </div>
              </div>

              <div className="settings-section">
                <span className="settings-section-title">4. Tùy Chọn Hiển Thị</span>
                <label className="event-checkbox-label">
                  <input
                    type="checkbox"
                    checked={enableSkeleton}
                    onChange={(e) => setEnableSkeleton(e.target.checked)}
                  />
                  <span>Vẽ khung xương cơ thể và làm nổi bật 2 khớp cổ tay (Neon HUD)</span>
                </label>
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn-inline-action"
                onClick={() => setShowAiModal(false)}
              >
                Hủy
              </button>
              <button
                type="button"
                className="btn-inline-action primary"
                onClick={() => handleSaveAiConfig(modelMode, precisionMode, enableSkeleton)}
              >
                Áp Dụng & Tải Lại Mô Hình
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Telegram Alert Bot Modal */}
      {showTelegramModal && (
        <div className="modal-backdrop" onClick={() => setShowTelegramModal(false)}>
          <div className="settings-modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h4>Cảnh Báo Thông Minh Telegram Bot (@cameraAIyolo_bot)</h4>
                <span className="modal-time">Tự động chụp và gửi ảnh bằng chứng tức thời về điện thoại của bạn</span>
              </div>
              <button type="button" className="btn-close" onClick={() => setShowTelegramModal(false)}>×</button>
            </div>
            <div className="modal-body">
              {/* Security Lock Notice */}
              <div className="telegram-locked-box">
                <div className="telegram-locked-badge">
                  <span>🔒 ĐÃ KHÓA THÔNG BÁO ĐỘC QUYỀN</span>
                </div>
                <div style={{ marginTop: "6px" }}>
                  Hệ thống được thiết lập chỉ gửi thông báo & hình ảnh chứng cứ rổ tiền vào <strong>duy nhất 1 tài khoản</strong> được chỉ định bên dưới. Toàn bộ người lạ hoặc ID khác bị chặn hoàn toàn.
                </div>
              </div>

              {/* Locked User Card */}
              <div className="settings-section">
                <span className="settings-section-title">Tài Khoản Nhận Cảnh Báo (Cố Định Duy Nhất)</span>
                <div className="telegram-user-card">
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <div className="telegram-user-avatar">
                      DN
                    </div>
                    <div className="telegram-user-details">
                      <div className="telegram-user-name">@diep_nguyenk5</div>
                      <div className="telegram-user-id">
                        <span>Chat ID:</span>
                        <code>8269826134</code>
                        <span style={{ color: "#22C55E", fontSize: "0.75rem", marginLeft: "4px" }}>● Đã khóa bảo mật</span>
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn-inline-action"
                    onClick={handleTestTelegram}
                    disabled={testingTelegram}
                    title="Gửi thử một tin nhắn mẫu kiểm tra kết nối"
                    style={{ display: "flex", alignItems: "center", gap: "6px" }}
                  >
                    {testingTelegram ? "Đang gửi..." : "🔔 Gửi Thử"}
                  </button>
                </div>
              </div>

              {/* Activation Status */}
              <div className="settings-section">
                <span className="settings-section-title">Trạng Thái Kích Hoạt</span>
                <label className="event-checkbox-label" style={{ fontSize: "0.88rem", fontWeight: 600 }}>
                  <input
                    type="checkbox"
                    checked={telegramEnabled}
                    onChange={(e) => handleToggleTelegramEnabled(e.target.checked)}
                  />
                  <span>Bật gửi cảnh báo tức thời qua Telegram khi có thao tác ở rổ tiền</span>
                </label>
              </div>

              {/* Event Filter */}
              <div className="settings-section">
                <span className="settings-section-title">Loại Sự Kiện Muốn Nhận Báo Động</span>
                <div className="events-checklist">
                  {["RÚT TIỀN", "BỎ TIỀN", "CHẠM RỔ"].map((ev) => (
                    <label key={ev} className="event-checkbox-label">
                      <input
                        type="checkbox"
                        checked={telegramEvents.includes(ev)}
                        onChange={(e) => handleToggleTelegramEvent(ev, e.target.checked)}
                      />
                      <span>{ev}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn-inline-action"
                onClick={() => setShowTelegramModal(false)}
              >
                Đóng
              </button>
              <button
                type="button"
                className="btn-inline-action primary"
                onClick={() => handleSaveTelegramConfig(telegramEnabled, "8269826134", telegramEvents)}
              >
                Lưu Cấu Hình
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Evidence Detail Modal */}
      {selectedEvidence && (
        <div className="modal-backdrop" onClick={() => setSelectedEvidence(null)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h4>Chi tiết Bằng chứng: {selectedEvidence.event_type}</h4>
                <span className="modal-time">{new Date(selectedEvidence.timestamp).toLocaleString("vi-VN")}</span>
              </div>
              <button type="button" className="btn-close" onClick={() => setSelectedEvidence(null)}>×</button>
            </div>
            <div className="modal-body">
              <img src={selectedEvidence.image_url} alt={selectedEvidence.event_type} className="modal-full-img" />
              <div className="modal-details">
                <p><strong>Mô tả:</strong> {selectedEvidence.summary}</p>
                <p><strong>Độ tin cậy:</strong> {(selectedEvidence.confidence * 100).toFixed(0)}%</p>
                {selectedEvidence.duration_seconds > 0 && (
                  <p><strong>Thời lượng:</strong> {selectedEvidence.duration_seconds.toFixed(1)} giây</p>
                )}
              </div>
            </div>
            <div className="modal-footer">
              <a href={selectedEvidence.image_url} download={selectedEvidence.filename} className="btn-download">
                Tải ảnh gốc JPEG
              </a>
              <button type="button" className="btn-secondary" onClick={() => setSelectedEvidence(null)}>
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfirmModal {...confirmModal} />
    </div>
  );
};
