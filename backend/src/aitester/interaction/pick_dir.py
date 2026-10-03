"""本机目录选择器：由后端弹出系统的「选择文件夹」窗，返回真实绝对路径。

为什么是后端弹：绝对路径只有「拥有桌面的进程」能给，浏览器自己的 showDirectoryPicker 只暴露
末级文件夹名（File System Access API 的隐私限制，Chrome 的 getPath 提案一直没放行）。本后端与
浏览器同机，所以由它弹窗——等价于 Electron / Tauri 里 main 进程弹 dialog 再交给渲染进程。前提是
同机部署：后端搬到远端时弹的是服务器的窗。探针反查（前端落临时文件、后端扫盘找它）实测命中 9.7s
/ 未命中 30.8s，已弃用；PowerShell 的 WinForms FolderBrowserDialog 也试过，弹窗压不到浏览器上面
（实测抢前台后 dialog_on_top=False），tkinter 的 topmost 根窗实测能压住，且省掉一次子进程启动。

Tk 只能在创建它的线程里用，所以本模块只在同步端点（FastAPI 线程池线程）内调用。
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

from fastapi import APIRouter, HTTPException

from aitester.interaction.schemas import PickDirRequest, PickDirResponse

router = APIRouter(prefix="/api/fs")

DIALOG_TITLE = "选择项目本地文件目录"

# 多个 Tk 根窗并存是 tkinter 明确不支持的形态：同一时刻只允许一个选择器在跑
_PICK_LOCK = threading.Lock()


def _run_tk_picker(seed: str) -> str:
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        kwargs = {"parent": root, "title": DIALOG_TITLE, "mustexist": True}
        if seed:
            kwargs["initialdir"] = seed
        return filedialog.askdirectory(**kwargs) or ""
    finally:
        root.destroy()


def _seed_dir(raw: str) -> str:
    """种子无效时静默留空（弹窗退回上次位置），不拿一个假路径去激怒 tkinter。"""
    seed = (raw or "").strip()
    if not seed:
        return ""
    candidate = Path(seed).expanduser()
    return str(candidate) if candidate.is_dir() else ""


@router.post("/pick-dir", response_model=PickDirResponse)
def fs_pick_dir(req: PickDirRequest) -> PickDirResponse:
    """弹系统「选择文件夹」窗；`path` 是本机真实目录时作为初始位置。取消返回空 path。"""
    try:
        with _PICK_LOCK:
            chosen = _run_tk_picker(_seed_dir(req.path))
    except (tk.TclError, RuntimeError) as exc:
        # TclError＝没有可显示的桌面会话（服务态/远程）；RuntimeError＝Tk 被跨线程使用
        raise HTTPException(
            status_code=400, detail="本机目录选择器不可用，请直接粘贴绝对路径") from exc
    return PickDirResponse(path=chosen)
