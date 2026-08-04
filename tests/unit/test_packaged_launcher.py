from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis_backend import packaged_launcher
from jarvis_backend.packaged_launcher import build_runtime_config, worker_candidates
from jarvis_backend.settings import Settings


def test_packaged_runtime_config_uses_writable_data_paths(tmp_path: Path) -> None:
    data_root = tmp_path / "user data"
    worker = tmp_path / "runtime" / "jarvis-native-worker.exe"
    models = data_root / "models" / "MiniCPM-o-4_5-gguf"

    document = tomllib.loads(build_runtime_config(data_root, worker, models))
    settings = Settings.model_validate(
        {**document.pop("app"), **document}
    )

    assert settings.environment == "production"
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 31847
    assert settings.native.mode == "process"
    assert settings.native.pipe_name == r"\\.\pipe\AIJarvis.Worker.v1"
    assert settings.native.worker_path == worker.resolve()
    assert settings.native.model_path == models.resolve()
    assert settings.native.capture_interval_ms == 30_000
    assert settings.memory.root == (data_root / "memory").resolve()
    assert settings.courses.sessions_root == (data_root / "courses" / "sessions").resolve()


def test_packaged_runtime_config_accepts_an_isolated_server_port(tmp_path: Path) -> None:
    document = tomllib.loads(
        build_runtime_config(
            tmp_path / "data",
            tmp_path / "worker.exe",
            tmp_path / "models",
            server_port=43123,
        )
    )

    assert document["server"]["port"] == 43123


def test_packaged_runtime_config_accepts_an_isolated_pipe(tmp_path: Path) -> None:
    pipe_name = r"\\.\pipe\AIJarvis.Worker.test"
    document = tomllib.loads(
        build_runtime_config(
            tmp_path / "data",
            tmp_path / "worker.exe",
            tmp_path / "models",
            pipe_name=pipe_name,
        )
    )

    assert document["native"]["pipe_name"] == pipe_name


def test_packaged_runtime_config_accepts_capture_interval(tmp_path: Path) -> None:
    document = tomllib.loads(
        build_runtime_config(
            tmp_path / "data",
            tmp_path / "worker.exe",
            tmp_path / "models",
            capture_interval_ms=10_000,
        )
    )

    assert document["native"]["capture_interval_ms"] == 10_000


def test_packaged_runtime_prefers_cuda_and_keeps_cpu_fallback(tmp_path: Path) -> None:
    (tmp_path / "jarvis-native-worker-cuda.exe").touch()
    (tmp_path / "jarvis-native-worker-cpu.exe").touch()

    candidates = worker_candidates(tmp_path, nvidia_available=True)

    assert candidates == [
        ("cuda", tmp_path / "jarvis-native-worker-cuda.exe"),
        ("cpu", tmp_path / "jarvis-native-worker-cpu.exe"),
    ]


def test_packaged_runtime_uses_cpu_without_nvidia(tmp_path: Path) -> None:
    (tmp_path / "jarvis-native-worker-cuda.exe").touch()
    (tmp_path / "jarvis-native-worker-cpu.exe").touch()

    assert worker_candidates(tmp_path, nvidia_available=False) == [
        ("cpu", tmp_path / "jarvis-native-worker-cpu.exe")
    ]


def test_cuda_start_failure_falls_back_to_cpu(tmp_path: Path, monkeypatch) -> None:
    cuda_worker = tmp_path / "jarvis-native-worker-cuda.exe"
    cpu_worker = tmp_path / "jarvis-native-worker-cpu.exe"
    reference_audio = tmp_path / "default_ref_audio.wav"
    reference_audio.touch()
    processes = [object(), object()]
    spawned: list[Path] = []
    spawned_environments: list[dict[str, str]] = []
    terminated: list[object] = []
    progress: list[tuple[str, str, str]] = []

    def spawn(args, **kwargs):
        spawned.append(Path(args[0]))
        spawned_environments.append(kwargs["env"])
        return processes[len(spawned) - 1]

    def wait_for_pipe(worker, _pipe_name):
        if worker is processes[0]:
            raise RuntimeError("CUDA model initialization failed")

    monkeypatch.setattr(packaged_launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(packaged_launcher, "_wait_for_pipe", wait_for_pipe)
    monkeypatch.setattr(packaged_launcher, "_terminate", terminated.append)
    monkeypatch.setattr(
        packaged_launcher,
        "_runtime_backend_progress",
        lambda backend, message, reason="": progress.append((backend, message, reason)),
    )

    worker, path, backend, failure = packaged_launcher._start_native_worker(
        [("cuda", cuda_worker), ("cpu", cpu_worker)],
        r"\\.\pipe\AIJarvis.Worker.test",
        tmp_path / "models",
        tmp_path,
        {},
        object(),
    )

    assert worker is processes[1]
    assert path == cpu_worker
    assert backend == "cpu"
    assert "CUDA model initialization failed" in failure
    assert spawned == [cuda_worker, cpu_worker]
    assert all(
        environment["JARVIS_REF_AUDIO_PATH"] == str(reference_audio.resolve())
        for environment in spawned_environments
    )
    assert terminated == [processes[0]]
    assert progress[-1][0] == "cpu"
