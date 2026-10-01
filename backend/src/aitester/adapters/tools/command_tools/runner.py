"""命令执行内核：输出落临时文件、超时/超量终止进程树、有界回收与解码。

宿主执行语义对齐 QwenPaw ``agents/tools/shell.py``：
- stdout/stderr 重定向到临时文件而非管道——子进程继承管道句柄会让 ``communicate()``
  一直等到所有持有者关闭（如 Start-Process 拉起的后台进程），临时文件没有此问题；
- 回收上限 1MB（超出部分截断并附提示），落盘合计上限 10MB（轮询发现即终止进程树）；
- Windows 用 Job Object + ``taskkill /F /T`` 终止整棵树，POSIX 用进程组信号。
"""

from __future__ import annotations

import locale
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Sequence

from aitester.adapters.tools.command_tools.errors import raise_spawn_failed

# 单次回收给模型的输出上限（stdout 与 stderr 各自应用）。
OUTPUT_MAX_BYTES = 1024 * 1024

# stdout + stderr 临时文件合计落盘上限，轮询期间超出即终止进程树。
# 默认 10MB，可用环境变量 AITESTER_SHELL_MAX_OUTPUT_BYTES 覆盖。
OUTPUT_DISK_CAP_BYTES = int(
    os.environ.get("AITESTER_SHELL_MAX_OUTPUT_BYTES", 10 * 1024 * 1024)
)

_POLL_SECONDS = 0.2
_DISK_CHECK_EVERY_POLLS = 5
_PROCESS_REAP_SECONDS = 0.5
_PROCESS_KILL_REAP_SECONDS = 5.0
_POSIX_TERM_GRACE_SECONDS = 2.0


@dataclass
class CommandResult:
    """一次命令执行的回收结果。退出码 -1 表示被终止或启动失败。"""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_exceeded: bool = False
    duration_ms: int = 0


def smart_decode(data: bytes) -> str:
    """UTF-8 优先，失败回退本机首选编码（Windows 控制台多为 GBK），并去掉首尾换行。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        text = data.decode(encoding, errors="replace")
    return text.strip("\r\n")


# ─── 输出临时文件 ───────────────────────────────────────────────────────


def _open_temp_output(prefix: str) -> tuple[BinaryIO, str]:
    """POSIX：创建临时输出文件，返回（写句柄, 路径）。"""
    fd, path = tempfile.mkstemp(prefix=prefix)
    try:
        return os.fdopen(fd, "wb"), path
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _open_windows_temp_output(prefix: str) -> tuple[BinaryIO, BinaryIO]:
    """Windows：写句柄与独立读句柄都带 O_TEMPORARY，最后一个句柄关闭时自动删除。

    不继承管道，子进程退出后 ``proc.wait()`` 不会被仍持有句柄的后台后代拖住；
    独立读句柄有自己的文件位置，回收输出不会干扰仍在写的后代进程。
    """
    fd, name = tempfile.mkstemp(prefix=prefix)
    os.close(fd)
    writer: BinaryIO | None = None
    writer_fd: int | None = None
    reader_fd: int | None = None
    try:
        w_flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_TEMPORARY", 0)
        writer_fd = os.open(name, w_flags)
        writer = os.fdopen(writer_fd, "w+b")
        writer_fd = None

        r_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_TEMPORARY", 0)
        reader_fd = os.open(name, r_flags)
        reader = os.fdopen(reader_fd, "rb")
        reader_fd = None
        return writer, reader
    except BaseException:
        for fd_ in (reader_fd, writer_fd):
            if fd_ is not None:
                try:
                    os.close(fd_)
                except OSError:
                    pass
        if writer is not None:
            try:
                writer.close()
            except OSError:
                pass
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _read_output_snapshot(output_file: BinaryIO, max_bytes: int = OUTPUT_MAX_BYTES) -> str:
    """从输出文件读取一份固定、有界的快照；被截断时附提示。"""
    try:
        snapshot_size = os.fstat(output_file.fileno()).st_size
        capture_limit = max(0, max_bytes)
        capture_size = min(snapshot_size, capture_limit)
        output_file.seek(0)
        data = output_file.read(capture_size)
    except OSError:
        return ""

    text = smart_decode(data)
    if snapshot_size > len(data):
        notice = (
            f"⚠️ Output truncated: captured the first {len(data)} bytes "
            f"from a {snapshot_size}-byte snapshot "
            f"(limit: {capture_limit} bytes)."
        )
        return f"{text}\n{notice}" if text else notice
    return text


def _read_temp_file(path: str, max_bytes: int = OUTPUT_MAX_BYTES) -> str:
    """按路径读取一份固定、有界的输出快照。"""
    try:
        with open(path, "rb") as output_file:
            return _read_output_snapshot(output_file, max_bytes)
    except OSError:
        return ""


def _output_disk_size(
    stdout_path: str | None,
    stderr_path: str | None,
    stdout_reader: BinaryIO | None,
    stderr_reader: BinaryIO | None,
) -> int:
    """stdout + stderr 临时文件的合计字节数（Windows 读句柄 / POSIX 路径）。"""
    total = 0
    for reader, path in ((stdout_reader, stdout_path), (stderr_reader, stderr_path)):
        if reader is not None:
            try:
                total += os.fstat(reader.fileno()).st_size
            except OSError:
                pass
        elif path is not None:
            try:
                total += os.stat(path).st_size
            except OSError:
                pass
    return total


# ─── Windows 进程树终止 ──────────────────────────────────────────────────
# Job Object 保证后代（含 CREATE_NEW_PROCESS_GROUP / BREAKAWAY 脱离 PID 树的）
# 也能被 TerminateJobObject 一并终止；创建失败时退化为 taskkill /F /T。


def _create_job_object_win32():
    """创建 KILL_ON_JOB_CLOSE 的 Job Object，失败返回 None（优雅退化）。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.wintypes.DWORD),
                ("SchedulingClass", ctypes.wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _assign_process_to_job_win32(job_handle: Any, proc_handle: Any) -> bool:
    if job_handle is None:
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        return bool(kernel32.AssignProcessToJobObject(job_handle, proc_handle))
    except Exception:
        return False


def _terminate_job_win32(job_handle: Any) -> None:
    if job_handle is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.TerminateJobObject(job_handle, 1)
    except Exception:
        pass


def _close_job_handle_win32(job_handle: Any) -> None:
    """关闭句柄；KILL_ON_JOB_CLOSE 会在最后一个句柄关闭时清掉残余成员。"""
    if job_handle is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.CloseHandle(job_handle)
    except Exception:
        pass


def _kill_process_tree_win32(pid: int) -> None:
    """``taskkill /F /T`` 强杀整棵进程树（含 Popen.kill() 漏掉的孙进程）。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _kill_process_group_posix(proc: subprocess.Popen) -> None:
    """先给进程组 SIGTERM，宽限期内不退出再 SIGKILL。"""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_POSIX_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _kill_process_tree(proc: subprocess.Popen, job_handle: Any) -> None:
    """终止整棵进程树，并做有界回收（卡在内核 I/O 的子进程只赔一个句柄，不赔线程）。"""
    if sys.platform == "win32":
        _terminate_job_win32(job_handle)
        _kill_process_tree_win32(proc.pid)
    else:
        _kill_process_group_posix(proc)
    try:
        proc.wait(timeout=_PROCESS_REAP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=_PROCESS_KILL_REAP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ─── 自毁守卫 ────────────────────────────────────────────────────────────
# 智能体执行 ``taskkill /IM python.exe``、``kill $$`` 之类会把 AiTester 后端
# 连同终端一起带走，按 QwenPaw 的令牌级正则拦截，避免子串误伤（如 echo "kill python"）。

_DANGER_NAMES = {
    "python",
    "pythonw",
    "cmd",
    "powershell",
    "pwsh",
    "conhost",
    "bash",
    "sh",
}

# 命令起始处或 &&、;、| 之后的 kill/taskkill/stop-process 才算数。
_KILL_PREFIX = r"(?:^|[;&|]\s*)\s*"

# 按 PID 杀：taskkill /PID 123、kill -9 123、kill 123。
_KILL_PID_RE = re.compile(
    rf"{_KILL_PREFIX}(?:taskkill|kill|stop-process)\b.*(?:/PID|-p|-pid|\b)\s*(\d+)",
    re.IGNORECASE,
)

# 按镜像名杀：taskkill /IM python.exe、kill -9 powershell。
_DANGER_NAME_RE = re.compile(
    rf"{_KILL_PREFIX}(?:taskkill|kill|stop-process)\b.*?\b({'|'.join(sorted(_DANGER_NAMES))})(?:\.exe)?\b",
    re.IGNORECASE,
)

_SHELL_PID_VARS = {"$$", "$ppid", "$pid"}


def is_dangerous_self_kill(cmd: str) -> bool:
    """命令是否会终止当前进程或其父进程。"""
    lowered = cmd.lower()

    if _DANGER_NAME_RE.search(lowered):
        return True

    if "kill" in lowered or "stop-process" in lowered:
        if any(var in lowered for var in _SHELL_PID_VARS):
            return True

    match = _KILL_PID_RE.search(lowered)
    if match:
        try:
            target_pid = int(match.group(1))
        except ValueError:
            return False
        protected = {os.getpid()}
        if hasattr(os, "getppid"):
            protected.add(os.getppid())
        if target_pid in protected:
            return True

    return False


# ─── 执行入口 ────────────────────────────────────────────────────────────


def build_env() -> dict[str, str]:
    """子进程环境：继承 os.environ，并把当前解释器目录前置到 PATH（venv 里的 CLI 可直接调用）。"""
    env = os.environ.copy()
    python_bin_dir = str(os.path.dirname(sys.executable))
    existing = env.get("PATH", "")
    env["PATH"] = f"{python_bin_dir}{os.pathsep}{existing}" if existing else python_bin_dir
    return env


def run_command(
    argv: Sequence[str],
    cwd: str,
    timeout: float,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """执行命令并回收结果；超时或输出超量时终止整棵进程树。

    每个调用都是全新子进程，``cd`` / ``export`` / ``$env:`` 等设置不会跨调用保留。
    """
    started = time.monotonic()
    stdout_file: BinaryIO | None = None
    stderr_file: BinaryIO | None = None
    stdout_reader: BinaryIO | None = None
    stderr_reader: BinaryIO | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    proc: subprocess.Popen | None = None
    job_handle = None

    try:
        if sys.platform == "win32":
            stdout_file, stdout_reader = _open_windows_temp_output("aitester_out_")
            stderr_file, stderr_reader = _open_windows_temp_output("aitester_err_")
        else:
            stdout_file, stdout_path = _open_temp_output("aitester_out_")
            stderr_file, stderr_path = _open_temp_output("aitester_err_")

        popen_kwargs: dict[str, Any] = {
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": stderr_file,
            "text": False,
            "cwd": cwd,
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(list(argv), **popen_kwargs)

        if sys.platform == "win32":
            job_handle = _create_job_object_win32()
            # CPython 在 Windows 的 Popen 上暴露原生句柄
            proc_handle = getattr(proc, "_handle", None)
            if job_handle is not None and proc_handle is not None:
                _assign_process_to_job_win32(job_handle, proc_handle)

        # 子进程已继承各自副本，父进程副本及时关闭。
        stdout_file.close()
        stdout_file = None
        stderr_file.close()
        stderr_file = None

        deadline = time.monotonic() + max(0.0, timeout)
        timed_out = False
        output_exceeded = False
        polls = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                proc.wait(timeout=min(_POLL_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired:
                pass

            polls += 1
            if polls >= _DISK_CHECK_EVERY_POLLS:
                polls = 0
                if (
                    _output_disk_size(stdout_path, stderr_path, stdout_reader, stderr_reader)
                    > OUTPUT_DISK_CAP_BYTES
                ):
                    output_exceeded = True
                    break

        if timed_out or output_exceeded:
            _kill_process_tree(proc, job_handle)

        if stdout_reader is not None and stderr_reader is not None:
            stdout_str = _read_output_snapshot(stdout_reader)
            stderr_str = _read_output_snapshot(stderr_reader)
        else:
            stdout_str = _read_temp_file(stdout_path or "")
            stderr_str = _read_temp_file(stderr_path or "")
        duration_ms = int((time.monotonic() - started) * 1000)

        if output_exceeded:
            cap_msg = (
                f"⚠️ Command output exceeded the disk cap of {OUTPUT_DISK_CAP_BYTES} bytes "
                "and was terminated. Redirect the output to a file, or narrow the output and retry."
            )
            stderr_str = f"{stderr_str}\n{cap_msg}" if stderr_str else cap_msg
            return CommandResult(
                -1, stdout_str, stderr_str, output_exceeded=True, duration_ms=duration_ms
            )
        if timed_out:
            timeout_msg = (
                f"⚠️ Command execution exceeded the timeout of {timeout:g} seconds. "
                "Please consider increasing the timeout value if this command requires more time to complete."
            )
            stderr_str = f"{stderr_str}\n{timeout_msg}" if stderr_str else timeout_msg
            return CommandResult(-1, stdout_str, stderr_str, timed_out=True, duration_ms=duration_ms)

        returncode = proc.returncode if proc.returncode is not None else -1
        return CommandResult(returncode, stdout_str, stderr_str, duration_ms=duration_ms)

    except OSError as exc:
        if proc is not None:
            _kill_process_tree(proc, job_handle)
        raise_spawn_failed(exc)
    finally:
        _close_job_handle_win32(job_handle)
        for handle in (stdout_file, stderr_file, stdout_reader, stderr_reader):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        for path in (stdout_path, stderr_path):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
