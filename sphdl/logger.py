"""轻量彩色日志（纯标准库，线程安全，同时落盘 logs/sphdl.log）"""
import datetime
import os
import sys
import threading
from pathlib import Path


class Logger:
    COLORS = {
        "info": "\033[36m",
        "ok": "\033[32m",
        "warn": "\033[33m",
        "error": "\033[31m",
        "dim": "\033[2m",
    }
    RESET = "\033[0m"
    ROTATE_BYTES = 5 * 1024 * 1024  # 日志单文件上限 5MB（超出滚动为 .log.1）

    def __init__(self):
        self.lock = threading.Lock()
        self.debug_enabled = False
        self._subscribers = []
        self._log_file = None
        self._log_bytes = 0
        # 日志落在应用主目录下（开发=sphdl/logs/，冻结=exe 旁 logs/）
        from .config import BASE_DIR
        self._log_path = str(BASE_DIR / "logs" / "sphdl.log")

    def _open_log_file(self):
        """惰性打开落盘日志（追加+行缓冲）；失败则静默降级为仅控制台"""
        if self._log_file is None:
            try:
                os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
                self._rotate_if_large()
                f = open(self._log_path, "a", encoding="utf-8", buffering=1)
                f.write(f"\n===== session {datetime.datetime.now():%Y-%m-%d %H:%M:%S} pid={os.getpid()} =====\n")
                self._log_file = f
                self._log_bytes = os.path.getsize(self._log_path)
            except Exception:
                self._log_file = False
        return self._log_file or None

    def _rotate_if_large(self):
        """日志大小上限（默认 5MB）：超过则滚动为 sphdl.log.1（保留最近一份）。"""
        try:
            p = Path(self._log_path)
            if p.exists() and p.stat().st_size > self.ROTATE_BYTES:
                old = p.with_suffix(".log.1")
                if old.exists():
                    old.unlink()
                p.rename(old)
        except Exception:
            pass

    def _rotate_mid_session(self):
        """运行中滚动：关闭当前文件 → 改名 → 重新打开（保留最近一份）。"""
        try:
            f, self._log_file = self._log_file, None
            if f:
                f.close()
            self._rotate_if_large()
            self._open_log_file()
        except Exception:
            pass

    def _write_file(self, line):
        f = self._open_log_file()
        if f is None:
            return
        try:
            f.write(line + "\n")
            self._log_bytes += len(line) + 1
            if self._log_bytes > self.ROTATE_BYTES:
                self._rotate_mid_session()
        except Exception:
            pass

    def log_file(self, level, msg):
        """只落盘（GUI push_log 用：不进控制台、不触发订阅者，避免循环）"""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self._write_file(f"[{ts}] [{level.upper():<5}] {msg}")

    def subscribe(self, fn):
        """注册日志订阅者（GUI 等消费方），fn(level, msg)"""
        self._subscribers.append(fn)

    def _emit(self, level, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = self.COLORS.get(level, "")
        line = f"{color}[{ts}] {msg}{self.RESET}"
        with self.lock:
            # 冻结的无控制台 exe（--noconsole）里 sys.stdout 为 None，必须防护
            try:
                if sys.stdout is not None:
                    sys.stdout.write(line + "\n")
                    sys.stdout.flush()
            except Exception:
                pass
            self._write_file(f"[{ts}] [{level.upper():<5}] {msg}")
            for fn in list(self._subscribers):
                try:
                    fn(level, msg)
                except Exception:
                    pass

    def info(self, msg):
        self._emit("info", msg)

    def ok(self, msg):
        self._emit("ok", "✅ " + msg)

    def warn(self, msg):
        self._emit("warn", "⚠️ " + msg)

    def error(self, msg):
        self._emit("error", "❌ " + msg)

    def debug(self, msg):
        if self.debug_enabled:
            self._emit("dim", msg)


logger = Logger()
