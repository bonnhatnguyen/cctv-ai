# Tiếp tục V1 sau khi đổi tài khoản

## Mục tiêu đã duyệt

V1 chỉ xử lý video MP4 có sẵn bằng YOLO26n + ByteTrack để theo dõi **người**. Không làm realtime/RTSP, pose, tay, tiền, hàng hóa, hành động hoặc phát hiện bất thường trong V1.

## Vị trí làm việc

- Worktree: `C:\Users\Admin\Documents\Codex\2026-09-07\t\.worktrees\codex-v1-offline-person-tracking`
- Branch: `codex/v1-offline-person-tracking`
- Kế hoạch: `docs/superpowers/plans/2026-09-09-v1-offline-person-tracking-revised.md`
- Thiết kế: `docs/superpowers/specs/2026-09-09-v1-offline-person-tracking-design.md`
- Nhật ký SDD: `.superpowers/sdd/2026-09-09-v1-offline-person-tracking-revised/progress.md`

## Đã hoàn thành

- Task 1–2: pipeline YOLO26n + ByteTrack, overlay `người #ID`, H.264/yuv420p, kiểm tra đầy đủ frame/thời lượng và phát/tua trong trình duyệt.
- Task 3: API V1 riêng, tải MP4 có giới hạn dung lượng, SQLite job, worker tuần tự, phục hồi sau restart, phát Range và tải kết quả.
- Commit mới nhất của Task 3: `92cf2c9` (các commit sửa trước đó: `c80e648`, `3285586`, `4840ff3`).
- Kiểm thử gần nhất: V1 50 passed; toàn backend 69 passed.
- Luồng thật HTTP → worker → YOLO26n/CUDA đã đạt trên RTX 3060 Ti: 375 frame, 13.684 giây, 27.404 FPS, mean inference 5.931 ms; kết quả H.264/yuv420p, Range 206, download 200, file nguồn giữ nguyên.

## Trạng thái review Task 3

Ba vòng sửa đã xử lý worker bị chết do file khóa, shutdown khi thread còn sống, giới hạn upload quá muộn, chặn event loop và hủy upload để lại file mồ côi. Vòng re-review cuối chưa chạy được vì tài khoản hết usage. Việc đầu tiên khi tiếp tục là review diff `c80e648..92cf2c9`, đặc biệt hai ca cancellation cleanup/publication; nếu sạch thì ghi Task 3 complete vào ledger.

## Việc còn lại

1. Task 4: làm giao diện tiếng Việt chọn/thả MP4 → tải lên → nút `Bắt đầu theo dõi người` → trạng thái → video gốc/video có track → tải kết quả. Brief: `.superpowers/sdd/2026-09-09-v1-offline-person-tracking-revised/task-4-brief.md`.
2. Chạy kiểm thử frontend/build và luồng thật trong browser.
3. Task 5: tạo `start-v1.bat` + `scripts/start-v1.ps1`, hướng dẫn và nghiệm thu cuối.
4. Review tổng, verification đầy đủ; không tự merge vào `master`.

## Prompt ngắn cho tài khoản mới

> Tiếp tục V1 từ file `docs/V1-CONTINUE.md` trong worktree hiện tại. Không làm lại từ đầu và không mở rộng ngoài person-only offline MP4. Trước tiên re-review Task 3 commit `92cf2c9`, sau đó thực hiện Task 4 và Task 5 theo revised plan bằng TDD, review độc lập và kiểm thử video thật trong browser.
