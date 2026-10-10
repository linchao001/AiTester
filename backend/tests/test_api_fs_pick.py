"""`POST /api/fs/pick-dir` —— 后端弹本机系统「选择文件夹」窗，返回真实绝对路径。

为什么不让浏览器选：showDirectoryPicker 只给末级文件夹名，绝对路径只有拥有桌面的进程能给出。
探针反查（落临时文件+扫盘）实测命中 9.7s / 未命中 30.8s；PowerShell WinForms 弹窗压不到浏览器
上面（实测抢前台后 dialog_on_top=False）——两条都试过并弃用，细节见 pick_dir.py 模块注释。
"""

import tkinter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.interaction import pick_dir
from aitester.main import create_app


# 与 test_api_projects.py 同款：本端点不碰 reme，注入假 manager 免起真实实例
class _NoopKbManager:
    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return None

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return None


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        memory_manager=_NoopKbManager(),
    )
    return TestClient(app)


class _FakePicker:
    def __init__(self, *, returns="", exc=None):
        self.returns = returns
        self.exc = exc
        self.seeds = []

    def __call__(self, seed: str) -> str:
        self.seeds.append(seed)
        if self.exc:
            raise self.exc
        return self.returns


def _pick(client: TestClient, path: str = ""):
    return client.post("/api/fs/pick-dir", json={"path": path})


def test_pick_dir_returns_the_selected_absolute_path(client, monkeypatch):
    fake = _FakePicker(returns="D:/code/projects/order")
    monkeypatch.setattr(pick_dir, "_run_tk_picker", fake)
    resp = _pick(client)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "D:/code/projects/order"}


def test_pick_dir_cancel_is_200_with_empty_path(client, monkeypatch):
    """取消不是错误：窗关掉即返回空 path，由前端提示「未选择目录」。"""
    monkeypatch.setattr(pick_dir, "_run_tk_picker", _FakePicker(returns=""))
    resp = _pick(client)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": ""}


def test_pick_dir_seed_is_passed_only_when_it_is_a_real_directory(client, monkeypatch, tmp_path):
    real = tmp_path / "已有目录"
    real.mkdir()
    fake = _FakePicker(returns="D:/ok")
    monkeypatch.setattr(pick_dir, "_run_tk_picker", fake)
    assert _pick(client, str(real)).status_code == 200
    assert fake.seeds[-1] == str(real)
    assert _pick(client, str(tmp_path / "不存在")).status_code == 200
    assert fake.seeds[-1] == ""          # 无效种子静默留空，不拿假路径去激怒 tkinter
    assert _pick(client, "   ").status_code == 200
    assert fake.seeds[-1] == ""


def test_pick_dir_expands_tilde_in_seed(client, monkeypatch, tmp_path):
    """与创建校验、cwd 下发同口径：~ 开头的目录三层都得认。"""
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    fake = _FakePicker(returns="D:/ok")
    monkeypatch.setattr(pick_dir, "_run_tk_picker", fake)
    assert _pick(client, "~/proj").status_code == 200
    assert Path(fake.seeds[-1]).samefile(home / "proj")


def test_pick_dir_headless_tcl_error_is_400_not_500(client, monkeypatch):
    """无显示环境（服务态/远程）时 Tk() 抛 TclError——它直接继承 Exception，不是 RuntimeError。"""
    monkeypatch.setattr(pick_dir, "_run_tk_picker",
                        _FakePicker(exc=tkinter.TclError("no display name")))
    resp = _pick(client)
    assert resp.status_code == 400
    assert "粘贴" in resp.json()["detail"]


def test_pick_dir_cross_thread_runtime_error_is_400(client, monkeypatch):
    """Tk 被跨线程使用抛 RuntimeError——同样是「选择器不可用」，不该把 500 甩给用户。"""
    monkeypatch.setattr(pick_dir, "_run_tk_picker",
                        _FakePicker(exc=RuntimeError("Calling Tcl from different apartment")))
    resp = _pick(client)
    assert resp.status_code == 400
    assert "选择器" in resp.json()["detail"]


def test_pick_dir_serializes_concurrent_picks(client, monkeypatch):
    """多个 Tk 根窗并存是 tkinter 明确不支持的形态：并发请求必须排队，不能同弹两窗。"""
    import threading

    live = {"n": 0, "max": 0}
    gate = threading.Event()

    def slow(_seed: str) -> str:
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        gate.wait(timeout=5)
        live["n"] -= 1
        return "D:/ok"

    monkeypatch.setattr(pick_dir, "_run_tk_picker", slow)
    threads = [threading.Thread(target=lambda c=client: _pick(c)) for c in range(3)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(timeout=15)
    assert live["max"] == 1


def _has_display() -> bool:
    try:
        root = tkinter.Tk()
    except BaseException:                  # noqa: BLE001 - 无图形会话时 Tk 抛的就是 TclError
        return False
    root.destroy()
    return True


def test_real_tk_interpreter_works_off_the_main_thread():
    """tkinter 只能在创建它的线程里用，而同步端点跑在 FastAPI 的线程池线程上：真起一次解释器。"""
    import threading

    if not _has_display():
        pytest.skip("本机无可用图形会话，跳过真解释器用例")
    box = {}

    def call():
        try:
            root = tkinter.Tk()
            try:
                root.withdraw()
                root.attributes("-topmost", True)
                box["tk_ok"] = True
            finally:
                root.destroy()
        except BaseException as exc:       # noqa: BLE001 - 线程内的异常要带回主线程才看得见
            box["error"] = repr(exc)

    t = threading.Thread(target=call)
    t.start()
    t.join(timeout=20)
    assert box.get("error") is None, box
    assert box.get("tk_ok") is True, box
