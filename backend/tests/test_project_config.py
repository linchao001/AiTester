import pytest

from aitester.services.kb.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import (
    DESC_MAX,
    DIR_MAX,
    NAME_MAX,
    ProjectConfigError,
    ProjectService,
    visible_agent_ids,
)
from aitester.storage import FileJsonConfigRepository


@pytest.fixture()
def svc(tmp_path):
    return ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))


def _mk(svc, name="订单系统", **kw):
    kw.setdefault("desc", "交易链路")
    kw.setdefault("dir_", "D:/work/projects/order")
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
    assert svc.list_projects()[0]["session_count"] == 0


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


def test_dir_must_be_absolute_any_shape(svc):
    for bad in ("order-system", "work/projects/order", "relative\\path"):
        with pytest.raises(ProjectConfigError, match="绝对路径"):
            _mk(svc, name=f"坏{bad}", dir_=bad)
    for good in ("D:/work/a", "/home/me/a", "\\\\srv\\share\\a", "~/a"):
        assert _mk(svc, name=f"好{good}", dir_=good)["dir"]


def test_dir_strips_trailing_separators_but_keeps_root(svc):
    assert _mk(svc, name="尾斜杠", dir_="D:/work/a///")["dir"] == "D:/work/a"
    assert _mk(svc, name="纯根", dir_="D:/")["dir"] == "D:/"
    # 家目录根与裸盘符同病：剥尾分隔符塌成 `~` 会被 ABS_PATH 拒，须回退原输入形态
    assert _mk(svc, name="家目录正斜杠", dir_="~/")["dir"] == "~/"
    assert _mk(svc, name="家目录反斜杠", dir_="~\\")["dir"] == "~\\"


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
    # 冻结判据比的是清洗后的值：客户端整体提交可能带回尾分隔符，不得误判成「修改了目录」
    p = _mk(svc, dir_="D:/work/projects/order/")
    assert svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"],
                      dir_="D:/work/projects/order").get("dir") == p["dir"]


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
    q = _mk(svc, name="会员中心", dir_="D:/work/m")
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
