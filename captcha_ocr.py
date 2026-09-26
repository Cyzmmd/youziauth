"""四位数字验证码识别（纯 NumPy 前向推理，无深度学习框架依赖）。

用于 youziauth 的西南大学统一认证（IDM）静默登录：自动识别验证码后填入登录表单。

设计要点：
  * 推理只依赖 numpy + Pillow，且**不依赖项目内其他模块**，便于单独测试与打包；
  * 模型为 3 层卷积 + 4 个 softmax 头的迷你 CNN（约 15.9 万参数，578 KB），
    结构必须与训练侧 tools/captcha/net.py 完全一致（权重是按该结构保存的）；
  * **低置信拒答**：conf < min_conf 时返回 None，由调用方转人工。
    这一点很重要 —— 实测存在"高置信但错误"的样本（最高 conf=0.997 却是错的），
    而置信度闸门能把最难的一批（conf<0.9）挡掉，减少错码浪费登录尝试；
  * 模型对输入是确定性的：同一张图重复识别结果逐位相同，重复识别不会提升成功率，
    只有**重新获取一张新验证码**才构成新的独立机会。

训练与评估细节见 data/captcha/PROGRESS.md（交叉验证整串准确率 99.89%）。
"""

from __future__ import annotations

import io
import os
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 100, 30
# 与训练侧一致的归一化常数（见 tools/captcha/data.py）
PIX_MEAN, PIX_STD = 158.0, 60.0
DEFAULT_MIN_CONF = 0.9


def _resource_base() -> Path:
    """打包后资源位于 sys._MEIPASS，源码运行时位于本文件同级目录。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def default_model_path() -> Path:
    """模型文件位置；优先使用随包分发的 data/captcha/model.npz。"""
    override = os.environ.get("YOUZIAUTH_CAPTCHA_MODEL", "").strip()
    if override:
        return Path(override)
    return _resource_base() / "data" / "captcha" / "model.npz"


# --------------------------------------------------------------------------
# 与训练侧 tools/captcha/net.py 等价的前向实现（仅推理，去掉训练相关代码）
# --------------------------------------------------------------------------
def _im2col(x: np.ndarray, kh: int, kw: int) -> np.ndarray:
    n, c, h, w = x.shape
    oh, ow = h - kh + 1, w - kw + 1
    shape = (n, c, oh, ow, kh, kw)
    strides = (x.strides[0], x.strides[1], x.strides[2], x.strides[3], x.strides[2], x.strides[3])
    view = np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides)
    return view.transpose(0, 2, 3, 1, 4, 5).reshape(n * oh * ow, c * kh * kw)


class _Conv:
    """3x3 same-padding 卷积。"""

    def __init__(self, w: np.ndarray, b: np.ndarray):
        self.w, self.b = w, b
        self.pad = w.shape[2] // 2

    def __call__(self, x: np.ndarray) -> np.ndarray:
        # 必须用 C 连续数组：im2col 用 as_strided 依赖真实 strides
        x = np.ascontiguousarray(x, dtype=np.float32)
        n = x.shape[0]
        p = self.pad
        xp = np.zeros((n, x.shape[1], x.shape[2] + 2 * p, x.shape[3] + 2 * p), dtype=np.float32)
        if p:
            xp[:, :, p:p + x.shape[2], p:p + x.shape[3]] = x
        else:
            xp = x
        cols = _im2col(xp, self.w.shape[2], self.w.shape[3])
        out = cols @ self.w.reshape(self.w.shape[0], -1).T + self.b
        oh, ow = xp.shape[2] - self.w.shape[2] + 1, xp.shape[3] - self.w.shape[3] + 1
        return out.reshape(n, oh, ow, -1).transpose(0, 3, 1, 2)


def _maxpool2(x: np.ndarray) -> np.ndarray:
    n, c, h, w = x.shape
    oh, ow = h // 2, w // 2
    x = x[:, :, :oh * 2, :ow * 2]
    view = x.reshape(n, c, oh, 2, ow, 2).transpose(0, 1, 2, 4, 3, 5).reshape(n, c, oh, ow, 4)
    return view.max(axis=-1)


class CaptchaModel:
    """加载权重并执行前向推理。线程安全（每次调用不修改共享状态）。"""

    def __init__(self, weights: dict[str, np.ndarray]):
        self.c1 = _Conv(weights["c1.w"], weights["c1.b"])
        self.c2 = _Conv(weights["c2.w"], weights["c2.b"])
        self.c3 = _Conv(weights["c3.w"], weights["c3.b"])
        self.fc_w, self.fc_b = weights["fc.w"], weights["fc.b"]
        self.heads = [(weights[f"head{i}.w"], weights[f"head{i}.b"]) for i in range(4)]

    def logits(self, x: np.ndarray) -> np.ndarray:
        h = np.maximum(self.c1(x), 0)
        h = _maxpool2(h)
        h = np.maximum(self.c2(h), 0)
        h = _maxpool2(h)
        h = np.maximum(self.c3(h), 0)
        h = _maxpool2(h)
        h = h.reshape(h.shape[0], -1) @ self.fc_w + self.fc_b
        h = np.maximum(h, 0)
        return np.stack([h @ w + b for w, b in self.heads], axis=1)

    def predict_probs(self, x: np.ndarray) -> np.ndarray:
        z = self.logits(x)
        z = z - z.max(axis=-1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=-1, keepdims=True)


def _to_input(image_bytes: bytes) -> np.ndarray:
    """图片字节 -> (1,1,30,100) 归一化张量。尺寸不符时缩放（服务端应固定 100x30）。"""
    im = Image.open(io.BytesIO(image_bytes)).convert("L")
    if im.size != (W, H):
        im = im.resize((W, H), Image.BILINEAR)
    gray = np.asarray(im, dtype=np.float32)
    return ((gray - PIX_MEAN) / PIX_STD)[None, None, :, :].astype(np.float32)


class CaptchaSolver:
    """对外接口：solve(image_bytes) -> (code, confidence) 或 (None, confidence)。"""

    _lock = threading.Lock()

    def __init__(self, model_path: Path | str | None = None, min_conf: float = DEFAULT_MIN_CONF):
        self.model_path = Path(model_path) if model_path else default_model_path()
        self.min_conf = float(min_conf)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"验证码模型不存在: {self.model_path}")
        with np.load(self.model_path) as f:
            self.model = CaptchaModel({k: f[k] for k in f.files})

    def read(self, image_bytes: bytes) -> tuple[str, float]:
        """识别并返回 (4位码, 置信度)。置信度取 4 位中最低的一位（整串正确要求每位都对）。"""
        x = _to_input(image_bytes)
        with self._lock:  # NumPy 前向无共享状态，加锁仅防止极端并发下的内存峰值
            probs = self.model.predict_probs(x)[0]  # (4,10)
        digits = probs.argmax(axis=1)
        conf = float(probs.max(axis=1).min())
        return "".join(str(d) for d in digits), conf

    def solve(self, image_bytes: bytes) -> tuple[str | None, float]:
        """低置信返回 (None, conf)，表示应交人工处理。"""
        code, conf = self.read(image_bytes)
        if conf < self.min_conf:
            return None, conf
        return code, conf


_DEFAULT: CaptchaSolver | None = None
_DEFAULT_LOCK = threading.Lock()


def default_solver() -> CaptchaSolver:
    """进程内单例：首次调用约 30–135 ms，之后每次识别约 1.3 ms。"""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = CaptchaSolver()
    return _DEFAULT


def solve_captcha(image_bytes: bytes, min_conf: float = DEFAULT_MIN_CONF) -> tuple[str | None, float]:
    """便捷函数：低置信返回 None。"""
    return default_solver().solve(image_bytes) if min_conf == DEFAULT_MIN_CONF else CaptchaSolver(min_conf=min_conf).solve(image_bytes)
