from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import suppress
from itertools import count
from typing import Any

from .protocol import (
    HEADER,
    Frame,
    MessageType,
    ProtocolError,
    StatusCode,
    decode_frame,
    encode_frame,
    json_payload,
)

logger = logging.getLogger(__name__)


class NativeClient(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def events(self) -> AsyncIterator[dict[str, Any]]: ...


class InProcessNativeClient(NativeClient):
    """Deterministic worker stand-in used only by tests and explicit fake mode."""

    def __init__(self) -> None:
        self.running = False
        self._events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def start(self) -> None:
        self.running = True
        await self.emit({"type": "worker.ready", "inference_provider": "fake"})

    async def stop(self) -> None:
        if self.running:
            self.running = False
            await self._events.put(None)

    async def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.running:
            raise RuntimeError("native worker is not running")
        if method == "ping":
            return {"ok": True, "result": "pong"}
        return {"ok": True, "method": method, "result": payload}

    async def emit(self, event: dict[str, Any]) -> None:
        await self._events.put(event)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            yield event


class NamedPipeNativeClient(NativeClient):
    """Windows named-pipe transport using the same binary protocol as the C++ worker."""

    _METHODS = {
        "start_monitoring": MessageType.START,
        "resume_monitoring": MessageType.START,
        "pause_monitoring": MessageType.STOP,
        "stop_monitoring": MessageType.STOP,
        "ask": MessageType.SUBMIT,
        "cancel": MessageType.CANCEL,
        "shutdown": MessageType.SHUTDOWN,
        "ping": MessageType.HELLO,
        "set_game_profile": MessageType.CONFIGURE_GAME,
        "start_duplex": MessageType.START_DUPLEX,
        "stop_duplex": MessageType.STOP_DUPLEX,
    }

    def __init__(
        self,
        pipe_name: str,
        *,
        timeout: float = 5.0,
        capture_interval_ms: int = 30_000,
    ) -> None:
        self.pipe_name = pipe_name
        self.timeout = timeout
        self.capture_interval_ms = max(250, min(int(capture_interval_ms), 120_000))
        self.running = False
        self._stream: Any = None
        self._ids = count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._result_requests: set[int] = set()
        self._events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._windows_io_lock = threading.Lock()

    async def start(self) -> None:
        if self.running:
            return
        if not hasattr(asyncio, "windows_events") and __import__("os").name != "nt":
            raise RuntimeError("named-pipe native mode is available only on Windows")
        self._stream = await asyncio.to_thread(open, self.pipe_name, "r+b", buffering=0)
        self.running = True
        self._reader_task = asyncio.create_task(self._read_loop(), name="jarvis-native-pipe-reader")
        await self.request("ping", {})

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self._stream is not None:
            with suppress(Exception):
                await asyncio.to_thread(self._stream.close)
            self._stream = None
        if self._reader_task:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        error = RuntimeError("native pipe closed")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._result_requests.clear()
        await self._events.put(None)

    async def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.running or self._stream is None:
            raise RuntimeError("native worker is not running")
        try:
            message_type = self._METHODS[method]
        except KeyError as exc:
            raise ValueError(f"unsupported native command: {method}") from exc
        request_id = next(self._ids)
        if message_type == MessageType.SUBMIT:
            body = payload.get("text", "")
        elif message_type == MessageType.CONFIGURE_GAME:
            name = str(payload.get("name", ""))[:80]
            prompt = str(payload.get("prompt", ""))[:8000]
            body = f"{name}\0{prompt}"
        elif message_type == MessageType.START_DUPLEX:
            session_id = str(payload.get("session_id", ""))[:128]
            instruction = str(payload.get("instruction", ""))[:2000]
            body = f"{session_id}\0{instruction}"
        elif message_type == MessageType.START:
            body = {"capture_interval_ms": self.capture_interval_ms}
        else:
            body = payload
        raw = body.encode("utf-8") if isinstance(body, str) else json_payload(body)
        frame = encode_frame(Frame(message_type, request_id, raw))
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        if message_type == MessageType.SUBMIT:
            self._result_requests.add(request_id)
        async with self._write_lock:
            await asyncio.to_thread(self._write_exact, frame)
        try:
            request_timeout = max(
                0.1,
                min(600.0, float(payload.get("_timeout_seconds", self.timeout))),
            )
            return await asyncio.wait_for(future, request_timeout)
        finally:
            self._pending.pop(request_id, None)
            self._result_requests.discard(request_id)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._events.get()
            if item is None:
                break
            yield item

    async def _read_loop(self) -> None:
        try:
            while self.running and self._stream is not None:
                if os.name == "nt":
                    available = await asyncio.to_thread(self._windows_bytes_available)
                    if available < HEADER.size:
                        await asyncio.sleep(0.01)
                        continue
                header = await asyncio.to_thread(self._read_exact, HEADER.size)
                length = HEADER.unpack(header)[5]
                payload = await asyncio.to_thread(self._read_exact, length)
                frame = decode_frame(header + payload)
                data = self._decode_payload(frame)
                pending = self._pending.get(frame.request_id)
                if frame.message_type == MessageType.ERROR and pending:
                    message = str(data.get("error", "native worker error"))
                    pending.set_exception(RuntimeError(message))
                elif frame.message_type == MessageType.STATUS and pending:
                    if (
                        frame.request_id in self._result_requests
                        and frame.flags == StatusCode.CANCELLED
                    ):
                        pending.set_exception(RuntimeError("native inference was cancelled"))
                    elif frame.request_id not in self._result_requests:
                        pending.set_result(data)
                elif frame.message_type == MessageType.RESULT and pending:
                    pending.set_result(data)
                    await self._events.put(
                        {
                            "type": "answer.completed",
                            "request_id": frame.request_id,
                            **data,
                        }
                    )
                elif frame.message_type == MessageType.RESULT:
                    native_event = self._parse_native_event(frame.request_id, data)
                    if native_event is not None:
                        await self._events.put(native_event)
                    else:
                        event_type = (
                            "perception.completed"
                            if frame.request_id >= (1 << 63)
                            else "answer.completed"
                        )
                        await self._events.put(
                            {"type": event_type, "request_id": frame.request_id, **data}
                        )
                else:
                    await self._events.put(
                        {"type": "native.event", "request_id": frame.request_id, **data}
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.running:
                await self._events.put({"type": "worker.fatal", "error": str(exc)})
        finally:
            self.running = False

    def _read_exact(self, size: int) -> bytes:
        if os.name == "nt":
            return self._windows_read_exact(size)
        data = bytearray()
        while len(data) < size:
            chunk = self._stream.read(size - len(data))
            if not chunk:
                raise EOFError("native pipe closed")
            data.extend(chunk)
        return bytes(data)

    def _write_exact(self, data: bytes) -> None:
        if os.name == "nt":
            self._windows_write_exact(data)
            return
        view = memoryview(data)
        while view:
            written = self._stream.write(view)
            if not written:
                raise EOFError("native pipe closed")
            view = view[written:]

    def _windows_pipe_handle(self) -> int:
        import msvcrt

        return int(msvcrt.get_osfhandle(self._stream.fileno()))

    def _windows_read_exact(self, size: int) -> bytes:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = self._windows_pipe_handle()
        data = bytearray()
        with self._windows_io_lock:
            while len(data) < size:
                remaining = size - len(data)
                buffer = ctypes.create_string_buffer(remaining)
                transferred = ctypes.c_ulong()
                if not kernel32.ReadFile(
                    ctypes.c_void_p(handle),
                    buffer,
                    remaining,
                    ctypes.byref(transferred),
                    None,
                ):
                    error = ctypes.get_last_error()
                    if error in {109, 232}:  # ERROR_BROKEN_PIPE, ERROR_NO_DATA
                        raise EOFError("native pipe closed")
                    raise OSError(error, "ReadFile failed for native pipe")
                if transferred.value == 0:
                    raise EOFError("native pipe closed")
                data.extend(buffer.raw[: transferred.value])
        return bytes(data)

    def _windows_bytes_available(self) -> int:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        available = ctypes.c_ulong()
        with self._windows_io_lock:
            if not kernel32.PeekNamedPipe(
                ctypes.c_void_p(self._windows_pipe_handle()),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            ):
                error = ctypes.get_last_error()
                if error in {109, 232}:
                    raise EOFError("native pipe closed")
                raise OSError(error, "PeekNamedPipe failed for native pipe")
        return int(available.value)

    def _windows_write_exact(self, data: bytes) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = self._windows_pipe_handle()
        offset = 0
        with self._windows_io_lock:
            while offset < len(data):
                chunk = data[offset:]
                buffer = ctypes.create_string_buffer(chunk)
                transferred = ctypes.c_ulong()
                if not kernel32.WriteFile(
                    ctypes.c_void_p(handle),
                    buffer,
                    len(chunk),
                    ctypes.byref(transferred),
                    None,
                ):
                    error = ctypes.get_last_error()
                    raise OSError(error, "WriteFile failed for native pipe")
                if transferred.value == 0:
                    raise EOFError("native pipe closed")
                offset += transferred.value

    @staticmethod
    def _decode_payload(frame: Frame) -> dict[str, Any]:
        if not frame.payload:
            return {"ok": True}
        if frame.message_type == MessageType.RESULT:
            try:
                text = frame.payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning(
                    "native result %s contained invalid UTF-8 at byte %s; "
                    "replacing malformed bytes",
                    frame.request_id,
                    exc.start,
                )
                text = frame.payload.decode("utf-8", errors="replace")
            return {
                "ok": True,
                "text": text,
            }
        try:
            value = json.loads(frame.payload.decode("utf-8"))
            return value if isinstance(value, dict) else {"value": value}
        except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError):
            return {"ok": True, "text": frame.payload.decode("utf-8", errors="replace")}

    @staticmethod
    def _parse_native_event(
        request_id: int, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if request_id != 0xFFFFFFFFFFFFFFFF:
            return None
        text = data.get("text")
        if not isinstance(text, str):
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        topic = value.pop("native_event", None)
        if not isinstance(topic, str) or not topic:
            return None
        return {"type": topic, **value}
