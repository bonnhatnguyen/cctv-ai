# CCTV AI - Hệ Thống Giám Sát Quầy Thu Ngân & Rổ Tiền Thông Minh

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.3-61DAFB.svg)](https://react.dev/)
[![Ultralytics YOLO](https://img.shields.io/badge/YOLO-v11%20%7C%20v8-00FFFF.svg)](https://docs.ultralytics.com/)
[![MediaPipe](https://img.shields.io/badge/MediaPipe-Hands%2021--Points-FFA000.svg)](https://developers.google.com/mediapipe)
[![Tests Passing](https://img.shields.io/badge/tests-114%20passed-brightgreen.svg)]()

Hệ thống thị giác máy tính thông minh ứng dụng kiến trúc **Multi-Stage AI Pipeline** theo thời gian thực (Real-time Edge Vision) chuyên dụng cho giám sát quầy thu ngân, phát hiện hành vi tương tác rổ tiền mặt (Rút tiền, Bỏ tiền, Quét qua rổ), nhận diện mệnh giá tiền Việt Nam (VNĐ) và cảnh báo tức thời qua Telegram Bot kèm hình ảnh bằng chứng.

---

## 🤖 Dành Riêng Cho AI Assistant (Prompt for AI Helper)

> **💡 Dành cho bạn:** Nếu bạn đang nhờ AI (ChatGPT, Claude, Cursor, Copilot, Antigravity) hỗ trợ cấu hình hoặc vận hành dự án này, hãy copy đoạn bên dưới gửi cho AI:

```text
Tôi đang triển khai dự án CCTV AI (Hệ thống giám sát quầy thu ngân và rổ tiền thông minh).
- Backend: FastAPI, OpenCV, Ultralytics YOLO (Pose & Currency), Google MediaPipe Hands 21-Points, Roboflow Supervision (chạy cổng 8000).
- Frontend: React 18, Vite, TypeScript, TailwindCSS (chạy cổng 5173).
- Mã nguồn live streaming & phân tích nằm tại: backend/app/live/
- Giao diện giám sát & cài đặt: frontend/src/LiveWebcam.tsx
Hãy giúp tôi cấu hình file .env từ .env.example, hướng dẫn tôi tải trọng số models vào backend/weights/ và khởi động hệ thống.
```

---

## 🌟 Tính Năng Nổi Bật

### 1. Kiến Trúc AI Đa Tầng (Multi-Stage AI Pipeline)
- **YOLO11 Pose Estimation (Khớp xương người)**: Định vị chính xác tọa độ khớp cổ tay (Wrists #9, #10) của từng đối tượng khách/nhân viên, theo dõi chuyển động tiến vào/rời khỏi vùng rổ tiền.
- **Google MediaPipe 21-Points Hand Landmark**: Phân tích cấu trúc xương 3D của 21 khớp ngón tay trên vùng crop rổ tiền với tốc độ siêu tốc (~25ms), hiển thị khung xương Cyberpunk đa sắc màu.
- **Gesture Recognition (Nhận diện cử chỉ)**:
  - `Pinch` (Nhón ngón tay nhặt tiền - đo khoảng cách ngón cái #4 và ngón trỏ #8).
  - `Closed_Fist` (Nắm tay giữ tiền).
  - `Open_Palm` (Xòe tay thả tiền).
- **Nhận Diện Tiền Việt Nam VNĐ (Vietnamese Currency Detection)**: Nhận diện chính xác 9 mệnh giá tiền mặt Việt Nam (`1.000đ`, `2.000đ`, `5.000đ`, `10.000đ`, `20.000đ`, `50.000đ`, `100.000đ`, `200.000đ`, `500.000đ`) với bounding box xanh Neon Emerald và nhãn hiển thị trực tiếp.
- **Phát hiện Shoplifting / Trộm Quầy (ATM Theft Detection)**: Cảnh báo hành vi đáng ngờ xung quanh quầy.

### 2. Chuỗi Hành Vi Thông Minh (Action Sequence State Machine)
- **RÚT TIỀN**: Tay đưa vào rổ -> ngón tay nhón lại (`Pinch`) hoặc nắm (`Fist`) -> rút ra khỏi rổ -> Kích hoạt cảnh báo Rút tiền.
- **BỎ TIỀN**: Tay cầm tiền đưa vào -> xòe bàn tay (`Open_Palm`) thả tiền -> rút ra ngoài -> Ghi nhận Bỏ tiền/Thanh toán.
- **QUÉT QUA RỔ**: Tay chỉ lướt qua dọn dẹp hoặc chỉ trỏ mà không có hành vi nhón/nắm -> Phân loại quét qua, **loại bỏ 100% báo động giả**.
- **Majority Voting & Temporal Smoothing**: Thuật toán bỏ phiếu đa số theo thời gian giúp chốt mệnh giá tiền ổn định, loại bỏ hoàn toàn các khung hình nhiễu do motion blur khi vung tay.

### 3. Cảnh Báo Telegram Tức Thời (Real-time Telegram Bot)
- Đẩy hình ảnh snapshot độ phân giải cao kèm thông tin chi tiết:
  - 👤 **Đối tượng:** ID người tương tác
  - 🚨 **Sự kiện:** RÚT TIỀN / BỎ TIỀN / CHẠM RỔ
  - 🤏 **Cử chỉ bàn tay:** Pinch / Closed_Fist / Open_Palm
  - 💵 **Mệnh giá tiền phát hiện:** e.g. `20.000đ`, `500.000đ`
  - ⏱ **Thời lượng & Độ tin cậy**
- Cơ chế Queue bất đồng bộ (Non-blocking worker thread) đảm bảo việc gửi thông báo qua mạng không làm chậm FPS video stream.

---

## 📋 Hướng Dẫn Tự Cấu Hình Cho Bạn Của Bạn (2 Phút)

### Bước 1: Sao chép file cấu hình mẫu
Tại thư mục gốc dự án, sao chép file `.env.example` thành `.env`:
```powershell
# Trên Windows PowerShell:
Copy-Item .env.example .env

# Trên Linux / macOS:
cp .env.example .env
```

### Bước 2: Điền thông tin Bot Telegram vào file `.env`
Mở file `.env` vừa tạo:
```env
TELEGRAM_BOT_TOKEN=điền_token_bot_ở_đây
TELEGRAM_CHAT_ID=điền_chat_id_ở_đây
```

**Cách lấy Bot Token và Chat ID siêu dễ:**
1. **Lấy Token:** Mở Telegram, chat với bot [@BotFather](https://t.me/BotFather), gõ lệnh `/newbot`, đặt tên bot và username kết thúc bằng `_bot`. BotFather sẽ gửi cho bạn đoạn mã Token (dạng: `1234567890:AAH...`).
2. **Kích hoạt:** Tìm bot của bạn trên Telegram và bấm **START** (hoặc gửi tin nhắn `/start`).
3. **Lấy Chat ID:** 
   - *Cách 1 (Tự động):* Không cần điền `TELEGRAM_CHAT_ID`. Chỉ cần điền `TELEGRAM_BOT_TOKEN`, khởi động hệ thống, mở Web UI bấm nút **"Tự động nhận diện Chat ID"**!
   - *Cách 2:* Chat với bot [@userinfobot](https://t.me/userinfobot) trên Telegram, bot sẽ báo cho bạn biết số `Id` (ví dụ: `8269826134`). Điền số đó vào `TELEGRAM_CHAT_ID`.

---

## 📁 Cấu Trúc Dự Án

```
cctv-ai/
├── .env.example                      # File mẫu cấu hình biến môi trường
├── backend/
│   ├── .env.example                  # File mẫu cấu hình biến môi trường cho backend
│   ├── app/
│   │   ├── api.py                    # FastAPI root application
│   │   ├── live/
│   │   │   ├── api.py                # REST endpoints cho live camera, roi & telegram
│   │   │   ├── webcam.py             # LiveWebcamService, CashBasketTracker, EvidenceManager
│   │   │   ├── hand_analyzer.py      # MediaPipe 21-Points & Vietnamese Currency Detector
│   │   │   └── telegram.py           # TelegramNotifier bất đồng bộ
│   │   └── annotation/               # V2 Assisted labeling benchmark & annotation engine
│   ├── weights/                      # Nơi chứa các file trọng số mô hình AI (*.pt, *.onnx, *.task)
│   └── tests/                        # Toàn bộ test suite unit & integration
├── frontend/
│   ├── src/
│   │   ├── LiveWebcam.tsx            # Giao diện giám sát thời gian thực, HUD & Live ROI Editor
│   │   ├── App.tsx                   # Main Dashboard
│   │   └── annotation/               # Bộ công cụ gán nhãn và đánh giá benchmark
│   ├── package.json
│   └── vite.config.ts
├── scripts/                          # Các script khởi động và bảo trì (start, stop, clean)
└── .gitignore
```

---

## 🚀 Hướng Dẫn Cài Đặt & Chạy

### Yêu Cầu Hệ Thống
- Python: `>= 3.10` (khuyên dùng Python 3.11 hoặc 3.12)
- Node.js: `>= 18.0.0`
- Camera: Webcam USB, Camera tích hợp laptop hoặc Luồng RTSP IP Camera.

---

### 1. Cài Đặt Backend

```bash
# Di chuyển vào thư mục backend
cd backend

# Khởi tạo môi trường ảo Python
python -m venv .venv

# Kích hoạt môi trường ảo
# Trên Windows:
.venv\Scripts\activate
# Trên Linux/macOS:
source .venv/bin/activate

# Cài đặt các thư viện phụ thuộc
pip install -r requirements.txt
# hoặc cài đặt dạng editable package:
pip install -e .
```

### 2. Tải Trọng Số Mô Hình AI (Models / Weights)
Tạo thư mục `backend/weights/` (nếu chưa có) và đặt các file mô hình sau vào thư mục đó:

| File Mô Hình | Mục Đích | Nguồn Cung Cấp |
| :--- | :--- | :--- |
| `yolo11n-pose.pt` | Pose Skeleton & Cổ tay | [Ultralytics YOLO11-Pose](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt) |
| `gesture_recognizer.task` | 21 Khớp ngón tay & cử chỉ | [Google MediaPipe Hand Tasks](https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task) |
| `vietnamese_currency_yolo.pt` | Nhận diện tiền VNĐ (1k-500k) | Huấn luyện từ Roboflow Vietnamese Currency Dataset |
| `atm_theft_yolov8.pt` | Phát hiện trộm cắp / Shoplifting | ATM-Theft-Detection Dataset |

*(Ghi chú: Nếu file mô hình chưa có, hệ thống sẽ tự động chuyển sang chế độ dự phòng thông minh để không làm gián đoạn luồng chạy).*

### 3. Cài Đặt Frontend

```bash
cd frontend
npm install
```

---

### 4. Khởi Động Hệ Thống

#### Cách 1: Sử Dụng Script Tự Động (Khuyên Dùng)
Tại thư mục gốc dự án:
```powershell
# Chạy script PowerShell:
.\scripts\start-v1.ps1
# Hoặc chạy file batch:
start-v1.bat
```

#### Cách 2: Khởi Động Thủ Công

- **Terminal 1 - Backend:**
  ```bash
  cd backend
  .venv\Scripts\activate
  uvicorn app.api:app --reload --host 127.0.0.1 --port 8000
  ```

- **Terminal 2 - Frontend:**
  ```bash
  cd frontend
  npm run dev
  ```

Mở trình duyệt truy cập: **`http://localhost:5173`**

---

## 🧪 Kiểm Thử Hệ Thống (Automated Tests)

Toàn bộ hệ thống được bảo vệ bởi test suite tự động nghiêm ngặt:

- **Chạy kiểm thử Backend (Pytest):**
  ```bash
  cd backend
  pytest tests/unit/ -v
  ```
  *Kết quả: 39/39 tests PASSED (100%).*

- **Chạy kiểm thử Frontend (Vitest):**
  ```bash
  cd frontend
  npm test -- --run
  ```
  *Kết quả: 75/75 tests PASSED (100%).*

---

## 🛡️ Bản Quyền & Giấy Phép
Dự án được phát triển phục vụ mục đích giám sát an ninh quầy thu ngân thông minh và hỗ trợ gán nhãn dữ liệu thị giác máy tính.
Mọi đóng góp (Pull Request / Issue) đều được hoan nghênh!
