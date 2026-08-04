from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    bearer_token: str | None = None


class NativeSettings(BaseModel):
    mode: Literal["fake", "process"] = "fake"
    protocol_version: int = Field(default=1, ge=1, le=255)
    pipe_name: str = r"\\.\pipe\AIJarvis.Worker.v1"
    worker_path: Path = Path("build/native/Release/jarvis-native-worker.exe")
    model_path: Path = Path("models/MiniCPM-o-4_5-gguf")
    request_timeout_seconds: float = Field(default=5.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=10.0, gt=0)
    max_frame_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    capture_interval_ms: int = Field(default=30_000, ge=250, le=120_000)


class SceneSettings(BaseModel):
    enter_threshold: float = Field(default=0.72, ge=0, le=1)
    exit_threshold: float = Field(default=0.48, ge=0, le=1)
    enter_samples: int = Field(default=3, ge=1)
    exit_samples: int = Field(default=4, ge=1)
    display_enter_samples: int = Field(default=2, ge=1)
    game_enter_samples: int = Field(default=2, ge=1)
    display_exit_samples: int = Field(default=2, ge=1)
    game_exit_samples: int = Field(default=1, ge=1)
    game_uncertain_exit_samples: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def thresholds_have_hysteresis(self) -> SceneSettings:
        if self.exit_threshold >= self.enter_threshold:
            raise ValueError("exit_threshold must be below enter_threshold")
        return self


class BarrageSettings(BaseModel):
    max_age_seconds: float = Field(default=8.0, gt=0)
    max_queue_size: int = Field(default=256, ge=1)


class InteractionSettings(BaseModel):
    ordinary_bubble_cooldown_seconds: float = Field(default=16.0, ge=0)
    course_bubble_cooldown_seconds: float = Field(default=30.0, ge=0)
    game_barrage_repeat_seconds: float = Field(default=20.0, ge=0)
    game_barrage_similar_seconds: float = Field(default=4.0, ge=0)
    game_barrage_interval_seconds: float = Field(default=2.5, ge=0)


class MemorySettings(BaseModel):
    root: Path = Path("memory")
    activity_min_confidence: float = Field(default=0.6, ge=0, le=1)
    activity_min_interval_seconds: float = Field(default=120.0, ge=0)
    activity_duplicate_window_seconds: float = Field(default=900.0, ge=0)
    summary_timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class CourseSettings(BaseModel):
    sessions_root: Path = Path("courses/sessions")
    output_root: Path | None = None
    keyframe_min_interval_seconds: float = Field(default=30.0, ge=0)
    max_keyframes: int = Field(default=40, ge=1, le=200)
    exit_grace_seconds: float = Field(default=90.0, ge=0)
    exit_samples: int = Field(default=4, ge=1)


class Settings(BaseModel):
    name: str = "AI Jarvis"
    environment: str = "development"
    log_level: str = "INFO"
    server: ServerSettings = Field(default_factory=ServerSettings)
    native: NativeSettings = Field(default_factory=NativeSettings)
    scene: SceneSettings = Field(default_factory=SceneSettings)
    barrage: BarrageSettings = Field(default_factory=BarrageSettings)
    interaction: InteractionSettings = Field(default_factory=InteractionSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    courses: CourseSettings = Field(default_factory=CourseSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        root = Path(__file__).resolve().parents[2]
        config_path = path or Path(os.getenv("JARVIS_CONFIG", root / "config" / "default.toml"))
        raw: dict = {}
        if config_path.exists():
            with config_path.open("rb") as stream:
                document = tomllib.load(stream)
            raw = {**document.pop("app", {}), **document}
        overrides = {
            "environment": os.getenv("JARVIS_ENVIRONMENT"),
            "log_level": os.getenv("JARVIS_LOG_LEVEL"),
        }
        raw.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings.load()
