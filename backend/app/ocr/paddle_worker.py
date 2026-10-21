"""Private stdin/stdout PaddleOCR worker launched by paddle_engine."""
from __future__ import annotations
import argparse, base64, json, sys
from io import BytesIO
from PIL import Image
import numpy as np

def _result_dict(result):
    value = getattr(result, "json", None)
    if callable(value): value = value()
    if isinstance(value, dict): return value.get("res", value)
    value = result.get("res") if isinstance(result, dict) else getattr(result, "res", None)
    return value if isinstance(value, dict) else {}

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--model-name"); args = parser.parse_args()
    try:
        from paddleocr import PaddleOCR
        kwargs = {"use_doc_orientation_classify": False, "use_doc_unwarping": False,
                  "use_textline_orientation": False, "engine": "paddle"}
        if args.model_name: kwargs["text_detection_model_name"] = args.model_name
        ocr = PaddleOCR(**kwargs)
    except Exception: return 2
    for line in sys.stdin:
        try:
            request = json.loads(line); raw = base64.b64decode(request["image"], validate=True)
            image = Image.open(BytesIO(raw)).convert("RGB")
            payload = _result_dict(next(iter(ocr.predict(np.asarray(image)))))
            result = {"rec_texts": payload.get("rec_texts", []), "rec_scores": payload.get("rec_scores", []),
                      "rec_boxes": payload.get("rec_boxes", []), "rec_polys": payload.get("rec_polys", [])}
            sys.stdout.write(json.dumps({"ok": True, "result": result}, default=lambda value: value.tolist()) + "\n")
        except Exception: sys.stdout.write('{"ok":false}\n')
        sys.stdout.flush()
    return 0

if __name__ == "__main__": raise SystemExit(main())
