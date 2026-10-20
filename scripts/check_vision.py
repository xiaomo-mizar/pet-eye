# -*- coding: utf-8 -*-
"""检查当前 DeepSeek 账号是否支持图片输入，并真发一张图给 V4.1-Flash。

用法：
    .venv\\Scripts\\python.exe scripts\\check_vision.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_eye.capture import Shot
from pet_eye.config import load_config
from pet_eye.credentials import load_api_key
from pet_eye.llm import VisionLLM


def list_models(base_url: str, key: str) -> None:
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    print("模型列表：")
    for m in data.get("data", []):
        print(
            f"  - {m.get('id')}  name={m.get('name')}  "
            f"input_modalities={m.get('input_modalities')}  "
            f"context={m.get('context_window')}"
        )


def make_test_image() -> Shot:
    img = np.full((450, 800, 3), (40, 30, 20), np.uint8)
    cv2.rectangle(img, (60, 60), (520, 300), (180, 96, 28), -1)
    cv2.rectangle(img, (60, 60), (520, 300), (255, 255, 255), 4)
    cv2.putText(img, "WHALE TEST 42", (90, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    cv2.putText(img, "DeepSeek V4.1 Flash vision check", (90, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 230, 255), 2)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("测试图编码失败")
    return Shot(
        jpeg=buf.tobytes(),
        width=img.shape[1],
        height=img.shape[0],
        hash="0" * 16,
        captured_at=time.time(),
        monitor={"left": 0, "top": 0, "width": img.shape[1], "height": img.shape[0]},
    )


def main() -> int:
    cfg = load_config()
    key = load_api_key(cfg.llm.api_key_env)
    list_models(cfg.llm.base_url, key)
    print()
    shot = make_test_image()
    llm = VisionLLM(cfg.llm, key)
    result = llm.generate(shot, window_title="vision-check", idle_min=0, recent_lines=[])
    print("图片请求成功：")
    print("  台词：", result.line)
    print("  字数：", len(result.line))
    print("  tokens：", result.usage.get("total_tokens"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
