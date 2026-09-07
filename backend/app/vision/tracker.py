"""Small deterministic per-camera tracker; replaceable by ByteTrack in production."""

from collections import defaultdict
from dataclasses import replace

from app.schemas import Observation


class Tracker:
    def __init__(self, minimum_iou: float = 0.2):
        self.minimum_iou = minimum_iou
        self._next_id = 1
        self._previous: dict[str, list[Observation]] = defaultdict(list)

    def update(self, camera_id: str, observations: list[Observation]) -> list[Observation]:
        if any(item.camera_id != camera_id for item in observations):
            raise ValueError("observations must belong to the supplied camera")
        previous = self._previous[camera_id]
        result: list[Observation] = []
        used: set[int] = set()
        for observation in observations:
            candidate_index = self._best_match(observation, previous, used)
            if candidate_index is None:
                track_id = f"{camera_id}-{self._next_id}"
                self._next_id += 1
            else:
                used.add(candidate_index)
                track_id = previous[candidate_index].track_id
            result.append(replace(observation, track_id=track_id))
        self._previous[camera_id] = result
        return result

    def _best_match(self, observation, prior, used):
        choices = [(index, _iou(observation.bbox, candidate.bbox)) for index, candidate in enumerate(prior)
                   if index not in used and candidate.kind == observation.kind and observation.bbox and candidate.bbox]
        if not choices:
            return None
        index, score = max(choices, key=lambda choice: choice[1])
        return index if score >= self.minimum_iou else None


def _iou(a, b) -> float:
    if a is None or b is None:
        return 0
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return overlap / (area_a + area_b - overlap)
