from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
from pathlib import Path

import hf_xet
import uvicorn
from huggingface_hub.utils._runtime import is_xet_available

from jarvis_backend.app import create_app
from jarvis_backend.model_download import (
    DEFAULT_MIRROR_ENDPOINT,
    MODEL_REVISION,
    download_models,
    model_files_are_valid,
    model_marker_is_valid,
    write_model_marker,
)
from jarvis_backend.settings import Settings, get_settings

DEFAULT_SERVER_PORT = 31847
DEFAULT_PIPE_NAME = r"\\.\pipe\AIJarvis.Worker.v1"
MINIMUM_FREE_BYTES = 8 * 1024**3


def _progress(message: str) -> None:
    print(message, flush=True)


def _download_progress(completed: int, total: int) -> None:
    percent = min(100, max(0, int(completed * 100 / max(total, 1))))
    payload = {
        "type": "download-progress",
        "message": (
            f"正在下载模型：{completed / 1024**3:.2f} / {total / 1024**3:.2f} GiB"
        ),
        "completed": completed,
        "total": total,
        "percent": percent,
    }
    print(f"JARVIS_PROGRESS {json.dumps(payload, ensure_ascii=False)}", flush=True)


def _runtime_backend_progress(backend: str, message: str, reason: str = "") -> None:
    payload = {
        "type": "runtime-backend",
        "backend": backend,
        "message": message,
        "reason": reason,
    }
    print(f"JARVIS_PROGRESS {json.dumps(payload, ensure_ascii=False)}", flush=True)


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace", write_through=True)


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    configured = os.getenv("JARVIS_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable; cannot select a writable data folder")
    return (Path(local_app_data) / "AIJarvis").resolve()


def _model_root(data_root: Path) -> Path:
    configured = os.getenv("JARVIS_MODEL_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return data_root / "models" / "MiniCPM-o-4_5-gguf"


def _toml_path(path: Path) -> str:
    return path.resolve().as_posix().replace('"', '\\"')


def build_runtime_config(
    data_root: Path,
    worker_path: Path,
    model_root: Path,
    server_port: int = DEFAULT_SERVER_PORT,
    pipe_name: str = DEFAULT_PIPE_NAME,
) -> str:
    escaped_pipe_name = pipe_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'''[app]
name = "AI Jarvis"
environment = "production"
log_level = "INFO"

[server]
host = "127.0.0.1"
port = {server_port}

[native]
mode = "process"
protocol_version = 1
pipe_name = "{escaped_pipe_name}"
worker_path = "{_toml_path(worker_path)}"
model_path = "{_toml_path(model_root)}"
request_timeout_seconds = 120.0
heartbeat_interval_seconds = 10.0
max_frame_bytes = 8388608
capture_interval_ms = 30000

[scene]
display_enter_samples = 2
game_enter_samples = 2
display_exit_samples = 2
game_exit_samples = 1
game_uncertain_exit_samples = 2

[memory]
root = "{_toml_path(data_root / 'memory')}"

[interaction]
ordinary_bubble_cooldown_seconds = 16.0
course_bubble_cooldown_seconds = 30.0
game_barrage_repeat_seconds = 20.0
game_barrage_similar_seconds = 4.0
game_barrage_interval_seconds = 2.5

[courses]
sessions_root = "{_toml_path(data_root / 'courses' / 'sessions')}"
keyframe_min_interval_seconds = 30
max_keyframes = 40
exit_grace_seconds = 90
exit_samples = 4
'''


def _server_port() -> int:
    raw_value = os.getenv("JARVIS_SERVER_PORT", str(DEFAULT_SERVER_PORT))
    try:
        port = int(raw_value)
    except ValueError:
        raise RuntimeError("JARVIS_SERVER_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise RuntimeError("JARVIS_SERVER_PORT must be between 1 and 65535")
    return port


def _pipe_name() -> str:
    value = os.getenv("JARVIS_PIPE_NAME", "").strip()
    return value or rf"\\.\pipe\AIJarvis.Worker.{os.getpid()}"


def _ensure_models(model_root: Path) -> None:
    if model_marker_is_valid(model_root) and model_files_are_valid(model_root):
        _progress("本地模型校验通过")
        return

    data_drive = model_root if model_root.exists() else model_root.parent
    data_drive.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(data_drive).free < MINIMUM_FREE_BYTES:
        raise RuntimeError("可用磁盘空间不足，首次安装模型至少需要 8 GiB")

    if not model_files_are_valid(model_root):
        _progress("正在下载 MiniCPM-o 4.5 模型（约 6.32 GiB，可断点续传）")
        mirror = (
            None
            if os.getenv("JARVIS_DISABLE_DOWNLOAD_MIRROR", "").casefold()
            in {"1", "true", "yes", "on"}
            else os.getenv("JARVIS_HF_MIRROR", DEFAULT_MIRROR_ENDPOINT)
        )
        download_models(
            model_root,
            MODEL_REVISION,
            token=os.getenv("HF_TOKEN"),
            primary_endpoint=os.getenv("JARVIS_HF_PRIMARY_ENDPOINT", "https://huggingface.co"),
            mirror_endpoint=mirror,
            log=_progress,
            progress=_download_progress,
        )

    _progress("正在校验模型完整性，首次校验可能需要几分钟")
    if not model_files_are_valid(model_root, verify_hashes=True):
        raise RuntimeError("模型文件校验失败，请删除模型目录后重试")
    write_model_marker(model_root)
    _progress("模型准备完成")


def _wait_for_pipe(
    worker: subprocess.Popen[bytes],
    pipe_name: str,
    timeout_seconds: float = 600,
) -> None:
    wait_named_pipe = ctypes.windll.kernel32.WaitNamedPipeW
    wait_named_pipe.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(f"原生推理进程启动失败，退出代码 {worker.returncode}")
        if wait_named_pipe(pipe_name, 1000):
            return
        time.sleep(0.1)
    raise RuntimeError("原生推理进程未能在 10 分钟内完成初始化")


def _has_nvidia_driver() -> bool:
    if os.name != "nt":
        return False
    try:
        ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return False
    return True


def worker_candidates(
    runtime_root: Path,
    *,
    nvidia_available: bool | None = None,
) -> list[tuple[str, Path]]:
    cpu_worker = runtime_root / "jarvis-native-worker-cpu.exe"
    cuda_worker = runtime_root / "jarvis-native-worker-cuda.exe"
    legacy_worker = runtime_root / "jarvis-native-worker.exe"
    resolved_cpu = cpu_worker if cpu_worker.is_file() else legacy_worker
    has_nvidia = _has_nvidia_driver() if nvidia_available is None else nvidia_available

    candidates: list[tuple[str, Path]] = []
    if has_nvidia and cuda_worker.is_file():
        candidates.append(("cuda", cuda_worker))
    if resolved_cpu.is_file():
        candidates.append(("cpu", resolved_cpu))
    return candidates


def _start_native_worker(
    candidates: list[tuple[str, Path]],
    pipe_name: str,
    model_root: Path,
    data_root: Path,
    environment: dict[str, str],
    worker_log: object,
) -> tuple[subprocess.Popen[bytes], Path, str, str]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cuda_failure = ""
    for backend, worker_path in candidates:
        if backend == "cuda":
            _progress("检测到 NVIDIA 显卡，正在尝试 CUDA 加速")
        worker_environment = dict(environment)
        reference_audio = worker_path.parent / "default_ref_audio.wav"
        if reference_audio.is_file():
            worker_environment["JARVIS_REF_AUDIO_PATH"] = str(reference_audio.resolve())
        worker = subprocess.Popen(
            [str(worker_path), pipe_name, str(model_root)],
            cwd=data_root,
            env=worker_environment,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            _wait_for_pipe(worker, pipe_name)
        except Exception as exc:
            _terminate(worker)
            if backend != "cuda":
                raise
            cuda_failure = str(exc)
            _progress(f"CUDA 加速启动失败，正在自动切换到 CPU：{cuda_failure}")
            continue

        if backend == "cuda":
            _runtime_backend_progress("cuda", "NVIDIA CUDA 加速已启用")
        else:
            reason = cuda_failure or "未检测到可用的 NVIDIA CUDA 运行环境"
            _runtime_backend_progress(
                "cpu",
                "当前模型正在使用 CPU，推理体验会明显下降。",
                reason,
            )
        return worker, worker_path, backend, cuda_failure
    raise RuntimeError("安装包中没有可用的原生推理工作进程，请重新安装 AI Jarvis")


def _terminate(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _serve(config_path: Path) -> int:
    os.environ["JARVIS_CONFIG"] = str(config_path)
    get_settings.cache_clear()
    settings = Settings.load(config_path)
    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def _self_test() -> int:
    runtime_root = _runtime_root()
    required = [
        runtime_root / "jarvis-native-worker-cpu.exe",
        runtime_root / "jarvis-native-worker-cuda.exe",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"安装包运行时不完整：{', '.join(missing)}")
    Settings.model_validate({})
    ssl.create_default_context()
    if not callable(download_models):
        raise RuntimeError("模型下载组件不可用")
    if not callable(getattr(hf_xet, "download_files", None)):
        raise RuntimeError("Xet 模型下载组件不可用")
    importlib.metadata.version("hf-xet")
    if not is_xet_available():
        raise RuntimeError("Hugging Face 未识别到 Xet 模型下载组件")
    _progress("AI Jarvis 自包含运行时检查通过")
    return 0


def _launch() -> int:
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    runtime_root = _runtime_root()
    data_root = _data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    runtime_data = data_root / "runtime"
    runtime_data.mkdir(parents=True, exist_ok=True)
    model_root = _model_root(data_root)
    candidates = worker_candidates(runtime_root)
    if not candidates:
        raise RuntimeError("安装包缺少原生推理工作进程，请重新安装 AI Jarvis")

    _progress("正在检查本地模型")
    _ensure_models(model_root)
    server_port = _server_port()
    pipe_name = _pipe_name()
    environment = os.environ.copy()
    environment["PATH"] = f"{runtime_root}{os.pathsep}{environment.get('PATH', '')}"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    worker_log = (runtime_data / "native-worker.log").open("ab")
    worker: subprocess.Popen[bytes] | None = None
    backend: subprocess.Popen[bytes] | None = None
    try:
        _progress("正在启动本地推理服务")
        worker, worker_path, inference_backend, _ = _start_native_worker(
            candidates,
            pipe_name,
            model_root,
            data_root,
            environment,
            worker_log,
        )
        environment["JARVIS_ACTIVE_INFERENCE_BACKEND"] = inference_backend
        config_path = runtime_data / "real.toml"
        config_path.write_text(
            build_runtime_config(data_root, worker_path, model_root, server_port, pipe_name),
            encoding="utf-8",
        )
        backend = subprocess.Popen(
            [sys.executable, "--serve", str(config_path)],
            cwd=data_root,
            env=environment,
            creationflags=creation_flags,
        )
        _progress("本地服务已启动")
        while worker.poll() is None and backend.poll() is None:
            time.sleep(1)
        if backend.poll() is not None:
            _progress(f"后端服务进程异常退出，代码 {backend.returncode}")
            return int(backend.returncode or 0)
        _progress(f"原生推理进程异常退出，代码 {worker.returncode}")
        return int(worker.returncode or 0)
    finally:
        _terminate(backend)
        _terminate(worker)
        worker_log.close()


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser(description="AI Jarvis packaged runtime")
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    if args.serve:
        return _serve(args.serve)
    return _launch()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"启动失败：{error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
