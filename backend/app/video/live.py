"""Local RTSP-to-HLS workers. RTSP URLs stay in environment variables only."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


@dataclass
class LiveStream:
    camera_id: str
    status: str = "not_configured"
    process: subprocess.Popen | None = None


class LiveStreamManager:
    def __init__(self, output_root: str | Path, camera_env_keys: dict[str, str]):
        self.output_root = Path(output_root)
        self.camera_env_keys = camera_env_keys
        self.streams = {camera_id: LiveStream(camera_id) for camera_id in camera_env_keys}

    def start(self) -> None:
        ffmpeg = self._find_ffmpeg()
        for camera_id, env_key in self.camera_env_keys.items():
            stream = self.streams[camera_id]
            url = os.environ.get(env_key)
            if not url:
                stream.status = "not_configured"
                continue
            if not ffmpeg:
                stream.status = "ffmpeg_unavailable"
                continue
            target = self.output_root / camera_id
            target.mkdir(parents=True, exist_ok=True)
            stream.process = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
                "-an", "-c:v", "copy", "-f", "hls", "-hls_time", "2", "-hls_list_size", "6", "-hls_flags", "delete_segments",
                str(target / "index.m3u8")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stream.status = "starting"

    def statuses(self) -> dict[str, str]:
        for camera_id, stream in self.streams.items():
            if stream.process and stream.process.poll() is None and (self.output_root / camera_id / "index.m3u8").exists():
                stream.status = "live"
            elif stream.process and stream.process.poll() is not None:
                stream.status = "connection_failed"
        return {camera_id: stream.status for camera_id, stream in self.streams.items()}

    def stop(self) -> None:
        for stream in self.streams.values():
            if stream.process and stream.process.poll() is None:
                stream.process.terminate()

    @staticmethod
    def _find_ffmpeg() -> str | None:
        if executable := shutil.which("ffmpeg"):
            return executable
        package_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        candidates = list(package_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
        return str(candidates[0]) if candidates else None
