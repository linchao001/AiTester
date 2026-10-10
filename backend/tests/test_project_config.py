import os
import tempfile
from pathlib import Path

import pytest

from aitester.memory.reme.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import (
    DESC_MAX,
    DIR_MAX,
    NAME_MAX,
    ProjectConfigError,
    ProjectService,
    _clean_dir,
    dangerous_root_reason,
    dir_exists,
    visible_agent_ids,
)
from aitester.storage import FileJsonConfigRepository


@pytest.fixture()
def svc(tmp_path):
    return ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))


@pytest.fixture()
def allow_missing_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """形态/危险根用例专用：把存在性探测钉成 True。

    UNC「\\\\srv\\share\\a」与 POSIX「/home/me/a」在这台机器上造不出真实目录，而这些用例只锁
    形态判据；存在性的正反两面由 test_create_rejects_missing_dir / test_create_accepts 用真目录覆盖。
    """
    monkeypatch.setattr("aitester.services.project_config.dir_exists", lambda _dir: True)


def _mk(svc, name="订单系统", **kw):
    kw.setdefault("desc", "交易链路")
    # 创建时校验目录存在（2026-10-03 裁定）：默认给一个真实存在的目录，不插真目录的用例不再需要垫片
    kw.setdefault("dir_", tempfile.gettempdir())
    kw.setdefault("agents", [visible_agent_ids()[0]])
    return svc.create(name=name, **kw)


def test_seed_is_empty_and_persisted(tmp_path):
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    assert svc.list_projects() == []
    assert (tmp_path / "projects.json").exists()


def test_create_defaults_and_view_keys(svc):
    p = _mk(svc)
    assert set(p) == {"id", "name", "desc", "dir", "agents", "kb"}
    assert p["kb"] == PROJECT_KB_DEFAULT
    assert p["id"].startswith("proj_")
    # 恒 0 已从服务层摘掉：session_count 属「项目 × 会话」的组合事实，只由路由用 SessionStore 现算
    assert "session_count" not in svc.list_projects()[0]


def test_create_rejects_blank_and_overlong_fields(svc):
    first = _mk(svc)
    with pytest.raises(ProjectConfigError, match="请填写项目名称"):
        _mk(svc, name="   ")
    # 校验顺序：名称先于目录形态（空名 + 相对路径不得报「目录必须是绝对路径」）
    with pytest.raises(ProjectConfigError, match="请填写项目名称"):
        _mk(svc, name="   ", dir_="relative/path")
    with pytest.raises(ProjectConfigError, match="已存在同名项目"):
        _mk(svc, name=first["name"])
    with pytest.raises(ProjectConfigError, match=f"不能超过 {NAME_MAX} 字"):
        _mk(svc, name="项" * (NAME_MAX + 1))
    with pytest.raises(ProjectConfigError, match=f"不能超过 {DESC_MAX} 字"):
        _mk(svc, desc="描" * (DESC_MAX + 1))


def test_dir_must_be_absolute_any_shape(svc, allow_missing_dir):
    for bad in ("order-system", "work/projects/order", "relative\\path"):
        with pytest.raises(ProjectConfigError, match="绝对路径"):
            _mk(svc, name=f"坏{bad}", dir_=bad)
    for good in ("D:/work/a", "/home/me/a", "\\\\srv\\share\\a", "~/a"):
        assert _mk(svc, name=f"好{good}", dir_=good)["dir"]


def test_dir_strips_trailing_separators_but_keeps_root(svc, tmp_path, allow_missing_dir):
    assert _mk(svc, name="尾斜杠", dir_="D:/work/a///")["dir"] == "D:/work/a"
    nested = tmp_path / "work" / "b"
    assert _mk(svc, name="嵌套尾斜杠", dir_=f"{nested}///")["dir"] == str(nested)
    # 纯根形态清洗后仍保留原形状（`D:/` 不剥成裸盘符、`~` 不被 ABS_PATH 误判成相对路径），
    # 但自危险根闭集起，创建入口一律拒绝整盘/家目录本身（spec 裁定 6）
    assert _clean_dir("D:/") == "D:/"
    assert _clean_dir("~/") == "~/"
    assert _clean_dir("~\\") == "~\\"
    for pure_root in ("D:/", "~/", "~\\", os.path.abspath(os.sep)):
        with pytest.raises(ProjectConfigError, match="项目目录"):
            _mk(svc, name=f"纯根{pure_root}", dir_=pure_root)


def test_dir_overlong_rejected(svc):
    with pytest.raises(ProjectConfigError, match=f"不能超过 {DIR_MAX} 字"):
        _mk(svc, name="超长", dir_="D:/" + "x" * DIR_MAX)


def test_agents_required_deduped_and_whitelisted(svc):
    with pytest.raises(ProjectConfigError, match="请至少选择一个智能体"):
        _mk(svc, name="无智能体", agents=[])
    with pytest.raises(ProjectConfigError, match="未知智能体"):
        _mk(svc, name="假智能体", agents=["nope"])
    # 平台内置智能体不出现在可见目录，也不允许被项目启用
    assert "kb_assistant" not in visible_agent_ids()
    with pytest.raises(ProjectConfigError, match="不可启用"):
        _mk(svc, name="平台智能体", agents=["kb_assistant"])
    head = visible_agent_ids()[0]
    p = _mk(svc, name="去重", agents=[head, head])
    assert p["agents"] == [head]


def test_kb_alias_must_be_registered(svc):
    with pytest.raises(ProjectConfigError, match="未知知识库"):
        _mk(svc, name="坏KB", kb="zhb_kb")
    assert _mk(svc, name="默认KB")["kb"] == PROJECT_KB_DEFAULT


def test_view_never_leaks_real_kb_identity(svc):
    _mk(svc)
    blob = str(svc.list_projects()).lower()
    assert "zhb" not in blob


def test_get_unknown_id_raises_404_style(svc):
    with pytest.raises(ConfigNotFoundError, match="未知项目"):
        svc.get("proj_00000000")


def test_update_name_desc_agents_only(svc):
    p = _mk(svc)
    ids = visible_agent_ids()
    got = svc.update(p["id"], name="支付中心", desc="", agents=list(ids))
    assert got["name"] == "支付中心" and got["desc"] == ""
    # 可见目录当前只有 1 个智能体，白名单内不存在「换成另一个」的取值，
    # 故 agents 只锁「返回等于传入」，再用非法 id 锁编辑链路同样过白名单校验
    assert len(ids) == 1
    assert got["agents"] == ids
    with pytest.raises(ProjectConfigError, match="未知智能体"):
        svc.update(p["id"], name="支付中心", desc="", agents=[*ids, "ghost"])
    # 同名仍然唯一（排除自身后可用）
    _mk(svc, name="会员中心")
    with pytest.raises(ProjectConfigError, match="已存在同名项目"):
        svc.update(p["id"], name="会员中心", desc="", agents=ids)


def test_update_immutable_dir_and_kb_idempotent(svc):
    p = _mk(svc)
    # 幂等路径：整体提交原值要原样返回整个对象，不只是 id 对上
    assert svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"],
                      dir_=p["dir"], kb=p["kb"]) == p
    with pytest.raises(ProjectConfigError, match="本地文件目录创建后不可修改"):
        svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"], dir_="E:/other")
    # 被拒的调用不得部分改写
    after_dir_reject = svc.get(p["id"])
    assert after_dir_reject["dir"] == p["dir"] and after_dir_reject["kb"] == p["kb"]
    with pytest.raises(ProjectConfigError, match="知识库配置创建后不可修改"):
        svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"], kb="other")
    after_kb_reject = svc.get(p["id"])
    assert after_kb_reject["dir"] == p["dir"] and after_kb_reject["kb"] == p["kb"]
    assert after_dir_reject == p and after_kb_reject == p


def test_update_accepts_uncleaned_same_dir(svc):
    # 冻结判据比的是清洗后的值：喂带尾分隔符的原值，裸串比较会误判成「修改了目录」
    p = _mk(svc)
    assert svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"],
                      dir_=p["dir"] + "///")["dir"] == p["dir"]


def test_update_does_not_revalidate_frozen_dir(tmp_path):
    # 读侧自愈可能留下非绝对路径的历史 dir：编辑不得因此被「目录必须是绝对路径」锁死
    repo = FileJsonConfigRepository(tmp_path / "projects.json")
    repo.save({"version": 1, "projects": [
        {"id": "proj_aaaaaaaa", "name": "旧项目", "desc": "", "dir": "relative/old",
         "agents": [visible_agent_ids()[0]], "kb": "kb"}]})
    svc = ProjectService(repo)
    got = svc.update("proj_aaaaaaaa", name="旧项目改名", desc="", agents=visible_agent_ids())
    assert got["name"] == "旧项目改名" and got["dir"] == "relative/old"


def test_delete_last_project_protected(svc):
    p = _mk(svc)
    with pytest.raises(ProjectConfigError, match="至少需要保留 1 个项目"):
        svc.delete(p["id"])
    q = _mk(svc, name="会员中心")
    svc.delete(q["id"])
    assert [x["id"] for x in svc.list_projects()] == [p["id"]]
    with pytest.raises(ConfigNotFoundError, match="未知项目"):
        svc.delete(q["id"])


def test_normalization_self_heals_hand_edited_junk(tmp_path):
    repo = FileJsonConfigRepository(tmp_path / "projects.json")
    repo.save({"version": 1, "projects": [
        "not-a-dict",
        {"id": "evil/..", "name": "  坏 id  ", "dir": "D:/x", "agents": ["ghost"], "kb": "nope"},
        {"name": "", "dir": "", "agents": []},
    ]})
    svc = ProjectService(repo)
    rows = svc.list_projects()
    assert len(rows) == 2
    assert all(r["id"].startswith("proj_") for r in rows)
    assert all(r["kb"] == PROJECT_KB_DEFAULT for r in rows)
    assert all(set(r["agents"]) <= set(visible_agent_ids()) for r in rows)
    assert rows[1]["name"] == "未命名项目"
    # 自愈结果立即回写：磁盘不再留坏形状
    assert ProjectService(FileJsonConfigRepository(tmp_path / "projects.json")).list_projects() == rows


def test_persist_across_service_instances(svc, tmp_path):
    p = _mk(svc)
    again = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    assert [x["id"] for x in again.list_projects()] == [p["id"]]
    assert again.get(p["id"])["dir"] == p["dir"]


# ---------- 危险根闭集（spec 裁定 6：四类判据是闭集，不是会长大的黑名单）----------

def _svc(tmp_path):
    # 与 svc fixture 同款构造，供直接拿 tmp_path 的用例复用
    return ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))


def test_filesystem_root_is_refused() -> None:
    # os.path.abspath(os.sep) 在 Windows 给 "C:\\"、POSIX 给 "/"，两侧都是「没有父目录」的形态
    assert dangerous_root_reason(os.path.abspath(os.sep)) is not None


def test_home_dir_itself_is_refused() -> None:
    assert dangerous_root_reason(str(Path.home())) is not None


def test_windows_env_dir_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(pf))
    assert dangerous_root_reason(str(pf)) is not None


def test_posix_system_dir_is_refused() -> None:
    # 第四类在清洗后的原始形态上判等、不 resolve：所以在 Windows 上也照样拦得住
    assert dangerous_root_reason("/usr") is not None


@pytest.mark.parametrize("entry", [
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/boot", "/dev", "/home", "/root",
])
def test_all_posix_system_roots_refused_on_this_platform(entry: str) -> None:
    # 闭集 10 条在任何平台都要被执行到（判据不看 resolve 结果）；子目录不误杀
    assert dangerous_root_reason(entry) is not None
    assert dangerous_root_reason(f"{entry}/someone/project") is None


def test_nul_byte_dir_is_not_refused_nor_leaks_valueerror() -> None:
    # 含 NUL 字节的 dir 会让 resolve() 抛 ValueError（不是 OSError）：
    # 判据必须吞掉它并按「解析不了不等于危险」放行，绝不让 ValueError 外泄成 500
    assert dangerous_root_reason("~/a\x00") is None


def test_malformed_env_root_never_blocks_create(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 环境变量给出畸形值（含 NUL）时，同一次计算里的 env 侧 resolve 也要被护住：
    # 畸形 env 只能「不产生候选」，不能把每一次正常 create 变成 500。
    # Windows 的 os.environ 拒绝写入 NUL，这里换成普通 dict 才能把畸形值喂到 resolve()。
    poison = {**os.environ, "ProgramFiles": "C:/a\x00"}
    monkeypatch.setattr(os, "environ", poison)
    target = tmp_path / "work" / "ok"
    target.mkdir(parents=True)          # 创建时校验目录存在：先给真目录，本用例只锁畸形 env 不挡正常创建
    assert dangerous_root_reason(str(target)) is None
    created = _svc(tmp_path).create(
        name="正常项目", desc="", dir_=str(target), agents=["case_design"]
    )
    assert created["dir"] == str(target)


def test_ordinary_subdir_is_allowed(tmp_path) -> None:
    target = tmp_path / "work" / "reqs"
    assert dangerous_root_reason(str(target)) is None


def test_create_refuses_filesystem_root(tmp_path) -> None:
    svc = _svc(tmp_path)  # 复用本文件既有的 ProjectService 构造助手
    with pytest.raises(ProjectConfigError) as exc:
        svc.create(name="整盘", desc="", dir_=os.path.abspath(os.sep), agents=["case_design"])
    assert "项目目录" in exc.value.detail


def test_create_allows_nested_dir(tmp_path) -> None:
    root = tmp_path / "reqs"
    root.mkdir()
    created = _svc(tmp_path).create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])
    assert created["dir"] == str(root)


# ---------- 创建时目录必须真实存在（2026-10-03 用户裁定：不存在就拦在创建口）----------

def test_create_rejects_missing_dir(svc, tmp_path) -> None:
    ghost = tmp_path / "typed-wrong"
    with pytest.raises(ProjectConfigError, match="不存在或不是目录"):
        _mk(svc, name="路径打错", dir_=str(ghost))
    assert not ghost.exists()          # 校验只读：被拒也不许替用户把目录建出来


def test_create_rejects_file_as_dir(svc, tmp_path) -> None:
    afile = tmp_path / "reqs.md"
    afile.write_text("x", encoding="utf-8")
    with pytest.raises(ProjectConfigError, match="不存在或不是目录"):
        _mk(svc, name="指到文件", dir_=str(afile))


def test_create_accepts_tilde_dir_that_exists(svc, tmp_path, monkeypatch) -> None:
    # `~` 展开口径与读侧探测、发送侧 cwd 认同一个入口（HOME/USERPROFILE 双设，任何平台同一真实目录）；
    # 落盘仍是用户输入的原始形态，不展开、不迁移
    home = tmp_path / "fakehome"
    (home / "work" / "reqs").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert _mk(svc, name="波浪号", dir_="~/work/reqs")["dir"] == "~/work/reqs"


def test_create_rejects_tilde_dir_that_missing(svc, tmp_path, monkeypatch) -> None:
    home = tmp_path / "fakehome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with pytest.raises(ProjectConfigError, match="不存在或不是目录"):
        _mk(svc, name="波浪号缺目录", dir_="~/work/reqs")


# ---------- 目录可达只读探测（读侧：不建目录、不执法）----------

def test_dir_exists_reads_disk_without_creating_it(tmp_path) -> None:
    real = tmp_path / "reqs"
    real.mkdir()
    ghost = tmp_path / "typed-wrong"
    assert dir_exists(str(real)) is True
    assert dir_exists(str(ghost)) is False
    assert dir_exists("") is False
    assert not ghost.exists()  # 探测绝不建目录：write 工具会建，这里必须不建


def test_dir_exists_swallows_nul_byte_value_error(tmp_path) -> None:
    # NUL 字节让文件系统调用抛 ValueError（不是 OSError）：与危险根判据同款坑，
    # 探测必须按「不可达」回答，绝不能把用户填错的 dir 炸成 500
    assert dir_exists(f"{tmp_path / 'reqs'}\x00") is False
    assert dir_exists("\x00") is False


def test_dir_exists_swallows_value_error_raised_by_filesystem(monkeypatch) -> None:
    # 3.11 的 Path.is_dir() 内部就吞掉了 ValueError，所以 NUL 用例其实测不到 dir_exists
    # 自己的 except：这里直接注入抛 ValueError 的底层判据，锁住「探测绝不外泄异常」这条硬要求
    def boom(self) -> bool:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(Path, "is_dir", boom, raising=False)
    assert dir_exists("D:/work/projects/order") is False


def test_dir_exists_ignores_blank_dirs() -> None:
    # 读侧自愈可能把 dir 留成空串或纯空白：一律答「不可达」，不抛
    assert dir_exists("   ") is False
    assert dir_exists(None) is False  # 类型坏的历史数据也不该把 GET 炸掉
