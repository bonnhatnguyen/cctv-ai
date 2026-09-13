"""API router for live webcam, CCTV RTSP streaming, cash basket monitoring, and evidence audit."""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import cv2
from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .webcam import webcam_service
from .telegram import telegram_notifier

router = APIRouter(prefix="/api/v1/live/webcam", tags=["live-webcam"])
live_router = APIRouter(prefix="/api/v1/live", tags=["live-cctv"])

SIMULATED_VIDEOS_DIR = Path("data/v1/simulated_videos").resolve()
SIMULATED_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


class WebcamConfigUpdate(BaseModel):
    enable_traces: bool | None = None
    box_style: str | None = None
    enable_zone: bool | None = None
    enable_basket: bool | None = None
    flip_h: bool | None = None
    flip_v: bool | None = None
    enable_video_simulation: bool | None = None
    loop_video: bool | None = None
    model_mode: str | None = None
    precision_mode: str | None = None
    enable_skeleton: bool | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool | None = None
    telegram_events: list[str] | None = None


class BasketRoiUpdate(BaseModel):
    points: list[list[int]]


class EvidenceCaptureRequest(BaseModel):
    event_type: str = "CHỤP THỦ CÔNG"
    summary: str = "Chụp bằng chứng thủ công từ giao diện"


@router.get("/status")
@live_router.get("/status")
def get_webcam_status():
    return webcam_service.status()


@router.post("/start")
@live_router.post("/start")
def start_webcam(
    device_index: int | None = Query(default=None),
    source: str | None = Query(default=None),
    loop: bool = Query(default=True),
):
    target: Union[int, str] = 0
    if source is not None:
        target = source
    elif device_index is not None:
        target = device_index
    return webcam_service.start(source=target, loop=loop)


@router.post("/config")
@live_router.post("/config")
def update_webcam_config(cfg: WebcamConfigUpdate):
    return webcam_service.update_config(
        enable_traces=cfg.enable_traces,
        box_style=cfg.box_style,
        enable_zone=cfg.enable_zone,
        enable_basket=cfg.enable_basket,
        flip_h=cfg.flip_h,
        flip_v=cfg.flip_v,
        enable_video_simulation=cfg.enable_video_simulation,
        loop_video=cfg.loop_video,
        model_mode=cfg.model_mode,
        precision_mode=cfg.precision_mode,
        enable_skeleton=cfg.enable_skeleton,
        telegram_token=cfg.telegram_token,
        telegram_chat_id=cfg.telegram_chat_id,
        telegram_enabled=cfg.telegram_enabled,
        telegram_events=cfg.telegram_events,
    )


@router.get("/telegram/status")
@live_router.get("/telegram/status")
def get_telegram_status():
    return telegram_notifier.get_status()


@router.post("/telegram/detect-chat-id")
@live_router.post("/telegram/detect-chat-id")
def detect_telegram_chat_id():
    res = telegram_notifier.detect_chat_id()
    if res.get("success") and res.get("chat_id"):
        webcam_service.update_config(
            telegram_chat_id=res["chat_id"],
            telegram_enabled=True,
        )
    return res


@router.post("/telegram/test")
@live_router.post("/telegram/test")
def send_telegram_test():
    return telegram_notifier.send_test_message()


@router.post("/stop")
@live_router.post("/stop")
def stop_webcam():
    return webcam_service.stop()


@router.get("/frame.jpg")
@live_router.get("/frame.jpg")
def get_webcam_frame():
    """Returns the latest JPEG frame directly with no-cache headers."""
    jpeg = webcam_service.get_latest_frame()
    if jpeg is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/stream")
@live_router.get("/stream")
def get_webcam_stream():
    # If not started, start automatically on stream request
    status_data = webcam_service.status()
    if not status_data["running"]:
        webcam_service.start()

    return StreamingResponse(
        webcam_service.generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/zone/roi")
@live_router.post("/zone/roi")
def update_security_zone_roi(payload: BasketRoiUpdate):
    if len(payload.points) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tọa độ ROI vùng quầy an ninh cần ít nhất 3 điểm đa giác",
        )
    return webcam_service.set_zone_roi(payload.points)

# --- Cash Basket ROI Endpoints ---

@router.post("/basket/roi")
@live_router.post("/basket/roi")
def update_basket_roi(payload: BasketRoiUpdate):
    if len(payload.points) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tọa độ ROI rổ tiền cần ít nhất 3 điểm đa giác (polygon)",
        )
    return webcam_service.set_basket_roi(payload.points)


# --- Evidence Snapshot & Mini-Gallery Endpoints ---

@router.get("/evidence/list")
@live_router.get("/evidence/list")
def list_evidence():
    """Returns the list of recent audit snapshot metadata, newest first."""
    return {"items": webcam_service.list_evidence()}


@router.post("/evidence/capture")
@live_router.post("/evidence/capture")
def capture_evidence(payload: EvidenceCaptureRequest | None = None):
    """Manually captures the current video frame as an audit evidence snapshot."""
    event_type = payload.event_type if payload else "CHỤP THỦ CÔNG"
    summary = payload.summary if payload else "Chụp bằng chứng thủ công"
    snap = webcam_service.capture_snapshot(event_type=event_type, summary=summary)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Camera chưa hoạt động hoặc chưa có khung hình để chụp",
        )
    return snap


@router.get("/evidence/{filename}")
@live_router.get("/evidence/{filename}")
def get_evidence_image(filename: str):
    """Serves the full-resolution evidence snapshot image."""
    img_path = webcam_service.get_evidence_path(filename)
    if not img_path or not img_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy ảnh bằng chứng: {filename}",
        )
    return FileResponse(
        img_path,
        media_type="image/jpeg",
        filename=filename,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/evidence")
@live_router.delete("/evidence")
def clear_evidence():
    """Clears all audit evidence snapshots."""
    deleted_count = webcam_service.clear_evidence()
    return {"deleted_count": deleted_count, "status": "cleared"}


# --- Simulated Video Management Endpoints ---

@router.post("/video/upload")
@live_router.post("/video/upload")
async def upload_simulated_video(file: UploadFile = File(...)):
    """Uploads a video file (MP4, AVI, MOV, MKV) for live CCTV simulation."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Vui lòng chọn file video")
    ext = Path(file.filename).suffix.lower()
    if ext not in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ file video định dạng .mp4, .avi, .mov, .mkv, .webm",
        )

    clean_stem = "".join(c for c in Path(file.filename).stem if c.isalnum() or c in ("-", "_")).strip()
    if not clean_stem:
        clean_stem = "sim_video"
    video_id = f"{clean_stem}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    safe_filename = f"{video_id}{ext}"
    save_path = SIMULATED_VIDEOS_DIR / safe_filename

    # Stream write chunk by chunk
    try:
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        if save_path.exists():
            save_path.unlink()
        raise HTTPException(status_code=500, detail=f"Không thể lưu file video: {exc}")

    # Probe video metadata using cv2
    fps = 0.0
    frame_count = 0
    duration_sec = 0.0
    width = 0
    height = 0
    cap = cv2.VideoCapture(str(save_path))
    if cap.isOpened():
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if fps > 0:
            duration_sec = round(frame_count / fps, 1)
        cap.release()

    size_bytes = save_path.stat().st_size
    created_at = datetime.fromtimestamp(save_path.stat().st_ctime, tz=timezone.utc).isoformat()

    return {
        "video_id": video_id,
        "filename": file.filename,
        "filepath": str(save_path),
        "size_bytes": size_bytes,
        "duration_sec": duration_sec,
        "fps": round(fps, 1),
        "width": width,
        "height": height,
        "created_at": created_at,
    }


@router.get("/video/list")
@live_router.get("/video/list")
def list_simulated_videos():
    """Lists all uploaded simulation videos with metadata."""
    items = []
    for p in SIMULATED_VIDEOS_DIR.glob("*.*"):
        if p.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
            cap = cv2.VideoCapture(str(p))
            fps = 0.0
            frame_count = 0
            duration_sec = 0.0
            width = 0
            height = 0
            if cap.isOpened():
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                if fps > 0:
                    duration_sec = round(frame_count / fps, 1)
                cap.release()
            st = p.stat()
            items.append({
                "video_id": p.stem,
                "filename": p.name,
                "filepath": str(p),
                "size_bytes": st.st_size,
                "duration_sec": duration_sec,
                "fps": round(fps, 1),
                "width": width,
                "height": height,
                "created_at": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
            })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"items": items}


@router.delete("/video/{video_id}")
@live_router.delete("/video/{video_id}")
def delete_simulated_video(video_id: str):
    """Deletes a simulated video file."""
    matched = list(SIMULATED_VIDEOS_DIR.glob(f"{video_id}.*"))
    if not matched:
        raise HTTPException(status_code=404, detail="Không tìm thấy file video mô phỏng")
    for p in matched:
        try:
            p.unlink()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Không thể xóa file: {exc}")
    return {"status": "deleted", "video_id": video_id}
