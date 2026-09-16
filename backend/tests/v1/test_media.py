from __future__ import annotations

import subprocess
from dataclasses import replace

import cv2
import numpy as np


def test_real_encoded_output(encoded_three_frame_result):
    result = encoded_three_frame_result
    assert (result.codec, result.pixel_format) == ("h264", "yuv420p")
    assert (result.width, result.height, result.decoded_frames) == (640, 360, 3)


def test_validate_output_rejects_decoder_truncation(
    encoded_three_frame_video, encoded_three_frame_result, monkeypatch
):
    from app.v1 import media

    source = media.probe_video(encoded_three_frame_video)
    truncated = replace(encoded_three_frame_result, decoded_frames=2, timestamps=(0.0, 0.04))
    monkeypatch.setattr(media, "fully_decode_video", lambda _path: truncated)
    monkeypatch.setattr(media, "decoded_frame_count", lambda _path: 3)

    try:
        media.validate_output(source, 3, encoded_three_frame_video)
    except ValueError as exc:
        assert "frame" in str(exc).lower() or "trunc" in str(exc).lower()
    else:
        raise AssertionError("a premature decoder stop must be rejected")


def test_validate_output_rejects_sample_aspect_ratio_mismatch(
    encoded_three_frame_video, encoded_three_frame_result
):
    from app.v1 import media

    source = replace(
        media.probe_video(encoded_three_frame_video), sample_aspect_ratio="2:1"
    )
    try:
        media.validate_output(source, 3, encoded_three_frame_video)
    except ValueError as exc:
        assert "aspect" in str(exc).lower() or "sar" in str(exc).lower()
    else:
        raise AssertionError("sample aspect ratio mismatch should be rejected")


def test_probe_video_reports_rational_metadata(tmp_path):
    from app.v1.media import probe_video

    source = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 360))
    for _ in range(3):
        writer.write(np.zeros((360, 640, 3), dtype=np.uint8))
    writer.release()
    metadata = probe_video(source)
    assert (metadata.width, metadata.height) == (640, 360)
    assert (metadata.fps_num, metadata.fps_den) == (25, 1)
    assert metadata.frame_count_estimate == 3
    assert metadata.codec == "mpeg4"
    assert metadata.preview_supported is False


def test_h264_yuv420p_source_preview_is_supported(encoded_three_frame_video):
    from app.v1.media import probe_video

    assert probe_video(encoded_three_frame_video).preview_supported is True


def test_probe_video_rejects_odd_dimensions(tmp_path, monkeypatch):
    from app.v1 import media

    odd = tmp_path / "odd.mp4"
    odd.touch()
    monkeypatch.setattr(media, "_ffprobe_json", lambda _path: {
        "streams": [{"codec_name": "h264", "width": 641, "height": 360,
                      "r_frame_rate": "25/1", "avg_frame_rate": "25/1",
                      "duration": "1", "nb_frames": "25", "sample_aspect_ratio": "1:1"}]
    })
    try:
        media.probe_video(odd)
    except ValueError as exc:
        assert "odd" in str(exc).lower()
    else:
        raise AssertionError("odd dimensions should be rejected")


def test_probe_video_allows_one_startup_near_duplicate(tmp_path, monkeypatch):
    from app.v1 import media

    source = tmp_path / "startup-irregular.mp4"
    source.touch()
    monkeypatch.setattr(media, "_ffprobe_json", lambda _path: {
        "streams": [{"codec_name": "h264", "width": 640, "height": 360,
                      "r_frame_rate": "25/1", "avg_frame_rate": "25/1",
                      "duration": "0.12", "nb_frames": "3", "sample_aspect_ratio": "1:1"}]
    })
    monkeypatch.setattr(media, "_ffprobe_timestamps", lambda _path: [0.0, 0.000011, 0.040011])
    assert media.probe_video(source).fps_num == 25


def test_probe_video_rejects_sustained_variable_timing(tmp_path, monkeypatch):
    from app.v1 import media

    source = tmp_path / "vfr.mp4"
    source.touch()
    monkeypatch.setattr(media, "_ffprobe_json", lambda _path: {
        "streams": [{"codec_name": "h264", "width": 640, "height": 360,
                      "r_frame_rate": "25/1", "avg_frame_rate": "25/1",
                      "duration": "0.12", "nb_frames": "4", "sample_aspect_ratio": "1:1"}]
    })
    monkeypatch.setattr(media, "_ffprobe_timestamps", lambda _path: [0.0, 0.03, 0.06, 0.09])
    try:
        media.probe_video(source)
    except ValueError as exc:
        assert "variable" in str(exc).lower()
    else:
        raise AssertionError("sustained variable timing should be rejected")


def test_annotate_people_writes_vietnamese_id_label(person_tracker):
    from app.v1.media import annotate_people

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    people = person_tracker.track_frame(frame).people
    annotated = annotate_people(frame, people)
    assert annotated.shape == frame.shape
    assert not np.array_equal(annotated, frame)
