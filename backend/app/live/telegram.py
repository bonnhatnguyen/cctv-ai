"""Telegram Bot Notifier Service for CCTV Cash Basket Alerts."""

from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("cctv.live.telegram")

DEFAULT_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEFAULT_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "cameraAIyolo_bot")
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


class TelegramNotifier:
    """Asynchronous, non-blocking Telegram Bot alert sender."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        events: Optional[List[str]] = None,
        cooldown_seconds: float = 3.0,
    ):
        self.token = (token if token is not None else DEFAULT_BOT_TOKEN).strip()
        self.chat_id = (chat_id if chat_id is not None else DEFAULT_CHAT_ID).strip()
        self.enabled = enabled
        self.events = events or ["RÚT TIỀN", "BỎ TIỀN", "CHẠM RỔ"]
        self.cooldown_seconds = cooldown_seconds
        self.last_sent_time: float = 0.0

        # Background worker queue for non-blocking HTTP requests
        self._queue: queue.Queue = queue.Queue(maxsize=30)
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def update_config(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: Optional[bool] = None,
        events: Optional[List[str]] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Updates Telegram configuration dynamically."""
        if token is not None:
            self.token = token.strip()
        if chat_id is not None:
            self.chat_id = chat_id.strip()
        if enabled is not None:
            self.enabled = bool(enabled)
        if events is not None:
            self.events = list(events)
        if cooldown_seconds is not None:
            self.cooldown_seconds = float(cooldown_seconds)

        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Returns current Telegram bot status."""
        return {
            "configured": bool(self.token and self.chat_id),
            "enabled": self.enabled,
            "bot_username": DEFAULT_BOT_USERNAME,
            "has_token": bool(self.token),
            "chat_id": self.chat_id,
            "events": self.events,
            "cooldown_seconds": self.cooldown_seconds,
        }

    def detect_chat_id(self) -> Dict[str, Any]:
        """Queries getUpdates from Telegram Bot API to detect the chat_id of the most recent message."""
        if not self.token:
            return {"success": False, "message": "Chưa cấu hình Token Telegram Bot"}

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "message": f"Telegram API lỗi HTTP {resp.status_code}: {resp.text}",
                }

            data = resp.json()
            if not data.get("ok"):
                return {
                    "success": False,
                    "message": f"Lỗi Telegram: {data.get('description', 'Không xác định')}",
                }

            updates = data.get("result", [])
            if not updates:
                return {
                    "success": False,
                    "message": "Chưa tìm thấy tin nhắn nào. Vui lòng mở Telegram, tìm bot @cameraAIyolo_bot và bấm START (hoặc gửi /start), sau đó bấm nút này lại.",
                }

            # Prioritize authorized user @diep_nguyenk5 / ID 8269826134
            latest_chat = None
            sender_name = ""
            for upd in reversed(updates):
                msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member")
                if msg and "chat" in msg:
                    chat = msg["chat"]
                    uid = str(chat.get("id", ""))
                    username = str(chat.get("username", "")).lower()
                    if uid == "8269826134" or "diep_nguyenk5" in username:
                        latest_chat = chat
                        sender_name = chat.get("first_name") or chat.get("username") or "@diep_nguyenk5"
                        break

            if not latest_chat:
                for upd in reversed(updates):
                    msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member")
                    if msg and "chat" in msg:
                        chat = msg["chat"]
                        latest_chat = chat
                        sender_name = chat.get("first_name") or chat.get("title") or chat.get("username") or "Người dùng"
                        break

            if not latest_chat:
                return {
                    "success": False,
                    "message": "Không tìm thấy thông tin phòng chat trong các cập nhật gần đây.",
                }

            detected_id = str(latest_chat["id"])
            self.chat_id = detected_id
            self.enabled = True

            logger.info("Successfully detected Telegram chat_id=%s for user %s", detected_id, sender_name)
            return {
                "success": True,
                "chat_id": detected_id,
                "sender_name": sender_name,
                "message": f"Đã ghép nối thành công với tài khoản: {sender_name} (ID: {detected_id})",
            }
        except Exception as exc:
            logger.error("Error detecting Telegram chat_id: %s", exc)
            return {"success": False, "message": f"Lỗi kết nối tới Telegram: {exc}"}

    def send_test_message(self) -> Dict[str, Any]:
        """Sends an immediate test notification to verify communication."""
        if not self.token or not self.chat_id:
            return {"success": False, "message": "Vui lòng cấu hình Token và Chat ID trước khi gửi test."}

        text = (
            "🔔 <b>THỬ NGHIỆM KẾT NỐI CAMERA GIÁM SÁT AI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ Hệ thống CCTV AI đã kết nối thành công với tài khoản của bạn (<b>@diep_nguyenk5</b>)!\n"
            "🛡 <b>Trạng thái:</b> Sẵn sàng cảnh báo tương tác rổ tiền.\n"
            "🔒 <b>Bảo mật:</b> Đã khóa thông báo độc quyền cho Chat ID: <code>8269826134</code>\n"
            f"🕒 <b>Thời gian:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🤖 <i>@cameraAIyolo_bot phát triển bởi CCTV AI</i>"
        )

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return {"success": True, "message": "Đã gửi tin nhắn thử nghiệm thành công tới Telegram!"}
            return {"success": False, "message": f"Lỗi gửi tin nhắn (HTTP {resp.status_code}): {resp.text}"}
        except Exception as exc:
            return {"success": False, "message": f"Lỗi kết nối: {exc}"}

    def queue_alert(self, image_bytes: Optional[bytes], event_data: Dict[str, Any]) -> None:
        """Enqueues an alert for asynchronous background transmission."""
        if not self.enabled or not self.token or not self.chat_id:
            return

        event_type = str(event_data.get("event_type", "TƯƠNG TÁC RỔ TIỀN"))
        if self.events and event_type not in self.events:
            return

        now = time.time()
        if (now - self.last_sent_time) < self.cooldown_seconds:
            logger.debug("Telegram alert skipped due to cooldown (%s)", event_type)
            return

        self.last_sent_time = now

        item = {
            "image_bytes": image_bytes,
            "event_data": event_data,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("Telegram alert queue is full, dropping oldest item")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except Exception:
                pass

    def _worker_loop(self) -> None:
        """Background thread loop consuming alert queue."""
        while True:
            try:
                item = self._queue.get()
                self._dispatch_alert(item["image_bytes"], item["event_data"], item["timestamp"])
                self._queue.task_done()
            except Exception as exc:
                logger.error("Unhandled error in Telegram worker loop: %s", exc)
                time.sleep(1.0)

    def _dispatch_alert(self, image_bytes: Optional[bytes], event_data: Dict[str, Any], ts_str: str) -> None:
        """Performs the actual HTTP POST to send photo or message."""
        event_type = event_data.get("event_type", "TƯƠNG TÁC RỔ TIỀN")
        duration = event_data.get("duration_seconds", 0.0)
        person_id = event_data.get("person_id")
        confidence = event_data.get("confidence", 0.85)
        summary = event_data.get("summary", "")
        gesture = event_data.get("gesture")
        currency = event_data.get("detected_currency")

        icon = "🚨"
        if event_type == "RÚT TIỀN":
            icon = "🔴"
        elif event_type == "BỎ TIỀN":
            icon = "🟢"
        elif event_type == "CHẠM RỔ":
            icon = "🟡"

        extra_ai_lines = ""
        if gesture and gesture != "None":
            extra_ai_lines += f"🤏 <b>Cử chỉ bàn tay:</b> <code>{gesture}</code>\n"
        if currency:
            extra_ai_lines += f"💵 <b>Mệnh giá tiền:</b> <code>{currency}</code>\n"

        caption = (
            f"{icon} <b>CẢNH BÁO: {event_type}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Đối tượng:</b> Người #{person_id if person_id is not None else 1}\n"
            f"⏱ <b>Thời lượng:</b> <code>{duration:.1f}s</code>\n"
            f"🎯 <b>Độ tin cậy:</b> <code>{confidence * 100:.1f}%</code>\n"
            f"🕒 <b>Thời gian:</b> <code>{ts_str}</code>\n"
            f"{extra_ai_lines}"
            f"📝 <b>Mô tả:</b> {summary}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <i>CCTV AI - Hệ Thống Giám Sát Quầy Thu Ngân Thông Minh</i>"
        )

        try:
            if image_bytes and len(image_bytes) > 0:
                url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
                files = {"photo": ("alert.jpg", io.BytesIO(image_bytes), "image/jpeg")}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                resp = requests.post(url, data=data, files=files, timeout=12)
            else:
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                data = {
                    "chat_id": self.chat_id,
                    "text": caption,
                    "parse_mode": "HTML",
                }
                resp = requests.post(url, json=data, timeout=10)

            if resp.status_code == 200:
                logger.info("Successfully dispatched Telegram alert: %s", event_type)
            else:
                logger.warning("Failed to dispatch Telegram alert (HTTP %s): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Network error dispatching Telegram alert: %s", exc)


# Global singleton instance
telegram_notifier = TelegramNotifier()
