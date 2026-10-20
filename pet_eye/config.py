# -*- coding: utf-8 -*-
"""配置模型：全部用 Pydantic 校验，config.toml 只写想改的字段。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .paths import CONFIG_PATH


class LLMConfig(BaseModel):
    """DeepSeek / OpenAI 兼容视觉接口。"""

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    # V4.1-Flash 是推理模型：low 已足够看懂屏幕，且不会把输出预算吃光
    reasoning_effort: Literal["low", "high", "max"] = "low"
    max_tokens: int = Field(default=2000, ge=256, le=8192)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    timeout_sec: float = Field(default=90.0, ge=10.0, le=300.0)
    connect_retries: int = Field(default=2, ge=0, le=5)
    line_retries: int = Field(default=2, ge=0, le=5)
    json_mode: bool = True


class CaptureConfig(BaseModel):
    """OpenCV / mss 截屏参数。"""

    monitor: Literal["primary", "all"] = "primary"
    max_width: int = Field(default=1280, ge=640, le=2560)
    jpeg_quality: int = Field(default=80, ge=50, le=95)
    mask_pet: bool = True
    mask_blur: int = Field(default=41, ge=9, le=121)
    save_debug_shot: bool = False
    change_threshold: int = Field(default=4, ge=0, le=64)
    skip_if_unchanged: bool = False


class BubbleConfig(BaseModel):
    """Qt 气泡外观与位置。"""

    duration_sec: float = Field(default=6.0, ge=3.0, le=120.0)
    # 按字数自动延长停留时间（在 duration_sec 基础上，最多 14 秒）
    auto_duration: bool = True
    # 更醒目的暖色描边 + 弹入动画；想回到原来的素白气泡就设 false
    vivid: bool = True
    accent_color: str = "#5b9cff"
    fill_color: str = "#e9f3ff"
    text_color: str = "#22314d"
    max_width_px: int = Field(default=340, ge=180, le=520)
    font_size_px: int = Field(default=18, ge=12, le=30)
    padding_x_px: int = Field(default=20, ge=8, le=40)
    padding_y_px: int = Field(default=16, ge=6, le=40)
    gap_px: int = Field(default=12, ge=0, le=60)
    screen_margin_px: int = Field(default=18, ge=0, le=120)
    # 拖拽时气泡跟随桌宠的刷新间隔：33ms ≈ 30fps；越小越跟手，CPU 也越高
    follow_interval_ms: int = Field(default=33, ge=16, le=500)
    max_chars_per_line: int = Field(default=18, ge=8, le=30)
    # auto = 桌宠在左半屏就放右上，在右半屏就放左上
    side: Literal["auto", "left", "right"] = "auto"
    opacity: float = Field(default=0.97, ge=0.5, le=1.0)
    fade_ms: int = Field(default=180, ge=0, le=1000)
    # 表情包气泡的最大尺寸
    image_max_width_px: int = Field(default=230, ge=120, le=420)
    image_max_height_px: int = Field(default=280, ge=120, le=480)


class AgentConfig(BaseModel):
    """调度：默认每 60 秒看一次（可配置）。"""

    min_interval_min: float = Field(default=1.0, ge=1.0, le=720.0)
    max_interval_min: float = Field(default=1.0, ge=1.0, le=1440.0)
    first_delay_sec: float = Field(default=10.0, ge=0.0, le=3600.0)
    pause_when_idle: bool = False
    idle_threshold_min: float = Field(default=12.0, ge=1.0, le=240.0)
    retry_on_error_sec: float = Field(default=300.0, ge=30.0, le=3600.0)
    remember_last_lines: int = Field(default=12, ge=0, le=30)
    tray_notifications: bool = False

    @model_validator(mode="after")
    def _check_interval(self) -> "AgentConfig":
        if self.max_interval_min < self.min_interval_min:
            raise ValueError("max_interval_min 必须 >= min_interval_min")
        return self


class ContextConfig(BaseModel):
    """识别主人正在听什么 / 玩什么 / 做什么，并让话里带一点。"""

    enabled: bool = True
    media: bool = True
    games: bool = True


class DiversityConfig(BaseModel):
    """降低同质化：候选策略、去重、素材、屏幕变化。"""

    enabled: bool = True
    candidates: int = Field(default=3, ge=1, le=3)
    similarity_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    # 在听歌时允许“哼 1~2 句歌词”的候选策略
    music_lyrics: bool = True
    # 把屏幕变化区域写进提示词
    screen_digest: bool = True
    # 冷知识/小故事/提问素材库
    material_bank: bool = True
    # 上一轮问过的问题，下一轮可以追问
    callback: bool = True


class MemeConfig(BaseModel):
    """每隔几条发一张表情包。"""

    enabled: bool = True
    every: int = Field(default=5, ge=2, le=50)
    # 留空 = 扫描 ~/.dsh/meme-packs 下的全部表情包；也可以只写想用的包名
    packs: list[str] = Field(default_factory=list)


class ControlConfig(BaseModel):
    """桌宠右键菜单的本地控制接口。"""

    enabled: bool = True
    port: int = Field(default=8765, ge=1024, le=65535)


class ActivityConfig(BaseModel):
    """用户动向：注册表 + PowerShell 看主人在忙什么（只读，不碰文件内容）。"""

    enabled: bool = True
    # 注册表：Win+R 历史、地址栏路径、最近应用、Recent 目录
    registry: bool = True
    # PowerShell：进程/窗口快照（比较慢，单独缓存，不每轮都跑）
    powershell: bool = True
    ps_interval_min: float = Field(default=5.0, ge=0.5, le=120.0)
    ps_timeout_sec: float = Field(default=12.0, ge=3.0, le=60.0)
    # 每类最多取几条
    max_items: int = Field(default=6, ge=1, le=20)
    # 多久以内算“最近”
    recent_minutes: int = Field(default=240, ge=10, le=1440)
    # 这些路径/标题不采集（密码库、隐私目录）
    skip_keywords: list[str] = Field(
        default_factory=lambda: ["Password", "密码", "银行", "pay", "token", ".ssh", "key"]
    )


class MemoryConfig(BaseModel):
    """记忆：3 轮滑动窗口 + 标签/chunk 长期记忆 + 遗忘。"""

    enabled: bool = True
    # 短期：滑动窗口保留几轮（用户要求 3 轮）
    window_size: int = Field(default=3, ge=1, le=10)
    # 长期：标签数量上限、每个标签下的 chunk 上限
    max_tags: int = Field(default=40, ge=5, le=200)
    max_chunks_per_tag: int = Field(default=8, ge=1, le=30)
    # 每 24 小时遗忘一次：权重按天衰减，低于阈值的 chunk/标签直接删掉
    forget_interval_hours: float = Field(default=24.0, ge=1.0, le=240.0)
    decay_per_day: float = Field(default=0.6, ge=0.1, le=0.99)
    min_weight: float = Field(default=0.15, ge=0.01, le=1.0)
    # 遗忘后仍然多久没被用到的标签直接删
    forget_after_days: float = Field(default=7.0, ge=1.0, le=90.0)
    # 同类标签用向量相似度合并
    merge_similarity: float = Field(default=0.86, ge=0.5, le=0.99)
    # 每轮召回几条记忆塞进提示词
    recall_k: int = Field(default=3, ge=0, le=10)
    # 模型每一轮最多能写几条新记忆
    remember_per_turn: int = Field(default=2, ge=0, le=5)
    chunk_max_chars: int = Field(default=60, ge=10, le=120)


class PrivacyConfig(BaseModel):
    """隐私保护：这些前台窗口不去截图/不调模型。"""

    skip_window_keywords: list[str] = Field(
        default_factory=lambda: [
            "1Password",
            "Bitwarden",
            "KeePass",
            "LastPass",
            "密码",
            "银行",
            "支付",
            "支付宝",
            "微信支付",
        ]
    )
    skip_when_locked: bool = True


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    bubble: BubbleConfig = Field(default_factory=BubbleConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    diversity: DiversityConfig = Field(default_factory=DiversityConfig)
    meme: MemeConfig = Field(default_factory=MemeConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)

    def next_interval_sec(self, rng) -> float:
        return rng.uniform(self.agent.min_interval_min, self.agent.max_interval_min) * 60.0


def load_config(path: Path | None = None) -> Config:
    """读 config.toml；文件不存在时全部用默认值。"""
    p = path or CONFIG_PATH
    if not p.exists():
        return Config()
    with p.open("rb") as f:
        raw = tomllib.load(f)
    return Config.model_validate(raw)
