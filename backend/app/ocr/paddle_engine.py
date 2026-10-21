"""PaddleOCR adapter backed by the project-local optional runtime."""
from __future__ import annotations
import base64, json, os, select, subprocess, threading, time
from pathlib import Path
from typing import Any
from PIL import Image
from backend.app.ocr.engine import OcrError, OcrLine, OcrOutput, OcrWord

MAX_WORKER_IMAGE_BYTES = 10 * 1024 * 1024

class PaddleUnavailableError(OcrError):
    code = "PADDLEOCR_UNAVAILABLE"

def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

def _result_dict(result: Any) -> dict[str, Any]:
    value = _value(result, "json", None)
    if callable(value): value = value()
    if isinstance(value, dict): return value.get("res", value)
    value = _value(result, "res", None)
    return value if isinstance(value, dict) else {}

def _boxes(value: Any) -> list[tuple[int, int, int, int]]:
    boxes = []
    for polygon in value or []:
        points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
        if not points: continue
        if len(points) == 4 and not isinstance(points[0], (list, tuple)):
            boxes.append(tuple(int(point) for point in points)); continue
        xs, ys = zip(*[(int(point[0]), int(point[1])) for point in points])
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes

def _output(payload: dict[str, Any], width: int, height: int, duration_ms: int) -> OcrOutput:
    texts, scores = payload.get("rec_texts", []), payload.get("rec_scores", [])
    boxes, words = _boxes(payload.get("rec_boxes") or payload.get("rec_polys")), []
    for index, raw in enumerate(texts):
        text = str(raw or "").strip()
        if not text: continue
        box = boxes[index] if index < len(boxes) else (0, 0, width, height)
        try: score = float(scores[index]) * 100 if index < len(scores) else 0.0
        except (TypeError, ValueError): score = 0.0
        words.append(OcrWord(text, round(max(0.0, score), 1), *box))
    lines = [OcrLine(w.text, w.confidence, w.left, w.top, w.right, w.bottom, [w]) for w in words]
    return OcrOutput("\n".join(w.text for w in words), lines,
        round(sum(w.confidence for w in words) / len(words), 1) if words else None,
        len(words), "paddleocr", "", 0, 1.0, [], duration_ms)

class PaddleOCREngine:
    ENGINE_NAME = "paddleocr"
    def __init__(self, timeout_seconds: float = 20.0, model_name: str | None = None,
                 python_path: str | None = None, max_image_bytes: int = MAX_WORKER_IMAGE_BYTES) -> None:
        self.timeout_seconds = max(0.1, timeout_seconds); self.model_name = model_name
        self.max_image_bytes = min(max_image_bytes, MAX_WORKER_IMAGE_BYTES)
        root = Path(__file__).resolve().parents[3]
        configured = Path(python_path) if python_path else root / ".venv-paddle" / "bin" / "python"
        self.python_path = configured if configured.is_absolute() else root / configured
        self._process: subprocess.Popen[str] | None = None; self._lock = threading.Lock()

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None: return
        try:
            if process.stdin: process.stdin.close()
        except OSError: pass
        try: process.terminate(); process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try: process.kill()
            except OSError: pass

    def close(self) -> None:
        with self._lock: self._stop()
    def __del__(self) -> None: self._stop()

    def _start(self) -> subprocess.Popen[str]:
        if not self.python_path.is_file(): raise PaddleUnavailableError("Project-local PaddleOCR interpreter is unavailable")
        root = Path(__file__).resolve().parents[3]
        args = [str(self.python_path), "-m", "backend.app.ocr.paddle_worker"]
        if self.model_name: args += ["--model-name", self.model_name]
        try:
            process = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except OSError as exc: raise PaddleUnavailableError("PaddleOCR worker could not be started") from exc
        self._process = process; return process

    def _request(self, image: Image.Image) -> dict[str, Any]:
        from io import BytesIO
        buffer = BytesIO(); image.save(buffer, format="PNG", optimize=False); data = buffer.getvalue()
        if len(data) > self.max_image_bytes: raise PaddleUnavailableError("Image exceeds the PaddleOCR worker size limit")
        with self._lock:
            process = self._process or self._start()
            request = json.dumps({"image": base64.b64encode(data).decode("ascii")}, separators=(",", ":")) + "\n"
            try:
                assert process.stdin and process.stdout
                process.stdin.write(request); process.stdin.flush()
                ready, _, _ = select.select([process.stdout], [], [], self.timeout_seconds)
                if not ready: self._stop(); raise PaddleUnavailableError("PaddleOCR worker timed out")
                line = process.stdout.readline()
                if not line: self._stop(); raise PaddleUnavailableError("PaddleOCR worker exited unexpectedly")
                payload = json.loads(line)
            except (OSError, BrokenPipeError, ValueError, json.JSONDecodeError) as exc:
                self._stop(); raise PaddleUnavailableError("PaddleOCR worker returned an invalid response") from exc
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                self._stop(); raise PaddleUnavailableError("PaddleOCR worker inference failed")
            return payload

    def run(self, image: Image.Image) -> OcrOutput:
        started = time.perf_counter(); payload = self._request(image)
        result = payload.get("result")
        if not isinstance(result, dict): raise OcrError("PaddleOCR worker returned no result")
        return _output(result, image.width, image.height, round((time.perf_counter() - started) * 1000))
