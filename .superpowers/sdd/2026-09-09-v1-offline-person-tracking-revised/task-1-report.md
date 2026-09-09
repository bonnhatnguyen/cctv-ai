# Task 1 report

- Base/head: `codex/v1-offline-person-tracking` / Task 1 commit in this branch.
- Source: `1788853020000-1788853080000.mp4`, SHA256 `168C34E8FECEB308499EDE039399BC33C887EFF4BD5A83445204140E05B167C2`; excerpt source 20–35 s, evaluation 20–25 s.
- Model: `yolo26n.pt`, SHA256 `9B09CC8BF347F0FC8A5F7657480587F25DB09B34BF33B0652110FB03A8AD4FEF`.
- Runtime: Torch `2.11.0+cu128` / CUDA `12.8`, Ultralytics `8.4.145`, OpenCV `5.0.0`, FFmpeg `9.0.1`, device `cuda:0 NVIDIA GeForce RTX 3060 Ti`.
- CLI output: `data/evidence/tracked-excerpt-20-35.mp4` (SHA256 `9D202E507CE67A1E79A1F7A8F36B74456BF7B9E71969B76AA8823F25425A1691`), 960x1080, SAR 2:1, H.264/yuv420p, 375/375 decoded frames, 15.000 s.
- Summary: 375 frames, 8 local IDs, 375 inference samples, mean inference 5.638 ms, processing 10.259 s, effective 36.552 FPS.
- Evaluation: target gray-top track ID 1 present in 125/125 frames (0 switches, no >0.5 s loss); beginning/middle/end contact sheet and JSONL evidence inspected.
- Evidence: `data/evidence/tracked-excerpt-20-35.jsonl`; labels are Pillow-rendered `người #ID · confidence%` using `C:/Windows/Fonts/arial.ttf`.
- Tests: `pytest backend/tests -q` -> 29 passed; `pytest backend/tests/v1 -q` -> 10 passed.
- Gate: PASS for Task 1 implementation and real CUDA video; non-target IDs vary outside the selected interval as expected for local clip tracks.
