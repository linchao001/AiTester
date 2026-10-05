import pytest
from langchain_core.tools import ToolException

from aitester.case_design.reviewers import ReviewerError, run_reviewer
from aitester.case_design.schema import (
    ClaimsOut,
    CompareOut,
    EnumeratorOut,
    MatrixOut,
    ReviewOut,
)

REVIEW_JSON = '```json\n{"opinions": [], "resolutions": []}\n```'
EMPTY_JSON = "```json\n{}\n```"


class _StubTaskTool:
    """吃 build_child/drive/parallel/roster 四面的替身（真实面见 adapters/tools/subagent_tools/task.py）。"""

    def __init__(self, outputs, parallel=None, roster=None):
        self.outputs = list(outputs)
        self.parallel = parallel or {}
        self.roster = roster or {}
        self.built = []
        self.drives = []

    def build_child(self, agent_id):
        self.built.append(agent_id)
        return {"child_of": agent_id}

    def drive(self, child, brief, *, call_id, name, title, config, isolated=False):
        self.drives.append({"child": child, "brief": brief, "call_id": call_id,
                            "name": name, "title": title, "config": config,
                            "isolated": isolated})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def test_reviewer_parses_fence_and_forwards_drive_kwargs():
    tool = _StubTaskTool([REVIEW_JSON], parallel={"case_review": True})
    cfg = {"configurable": {"thread_id": "t1"}}
    model, text = run_reviewer(tool, "case_review", "简报原文", model_cls=ReviewOut,
                               call_id="rev-1", title="块审", config=cfg)
    assert isinstance(model, ReviewOut) and text == REVIEW_JSON
    assert tool.built == ["case_review"]
    d = tool.drives[0]
    assert (d["call_id"], d["title"], d["config"]) == ("rev-1", "块审", cfg)
    assert d["isolated"] is True               # 只读面（parallel=True）→ 派生 ns
    assert d["name"] == "case_review"          # name 未给时回落 agent_id
    assert d["brief"] == "简报原文"


def test_reviewer_widened_face_drives_un_isolated():
    """用户把评审子面配宽（parallel=False）→ 退回父 ns 挂起续跑通路（R14 同判据）。"""
    tool = _StubTaskTool([REVIEW_JSON], parallel={"case_review": False})
    run_reviewer(tool, "case_review", "b", model_cls=ReviewOut, call_id="rev-1c", title="块审")
    assert tool.drives[0]["isolated"] is False


def test_reviewer_name_falls_back_to_roster_display_name():
    tool = _StubTaskTool([REVIEW_JSON], parallel={"case_review": True},
                         roster={"case_review": {"name": "用例评审子智能体"}})
    run_reviewer(tool, "case_review", "b", model_cls=ReviewOut, call_id="rev-1d", title="块审")
    assert tool.drives[0]["name"] == "用例评审子智能体"


def test_reviewer_explicit_name_wins():
    tool = _StubTaskTool([REVIEW_JSON])
    run_reviewer(tool, "case_review", "b", model_cls=ReviewOut,
                 call_id="rev-1b", title="块审", name="用例评审")
    assert tool.drives[0]["name"] == "用例评审"


def test_reviewer_retries_once_with_r2_call_id():
    tool = _StubTaskTool(["这不是围栏", REVIEW_JSON])
    model, _ = run_reviewer(tool, "case_review", "简报", model_cls=ReviewOut,
                            call_id="rev-2", title="块审")
    assert isinstance(model, ReviewOut)
    assert tool.built == ["case_review", "case_review"]   # 重试重建干净子实例
    assert [d["call_id"] for d in tool.drives] == ["rev-2", "rev-2-r2"]
    retry_brief = tool.drives[1]["brief"]
    assert retry_brief.startswith("简报") and "```json" in retry_brief


def test_reviewer_raises_after_two_bad_outputs():
    tool = _StubTaskTool(["坏的", "还是坏的"])
    with pytest.raises(ReviewerError, match="case_review"):
        run_reviewer(tool, "case_review", "简报", model_cls=ReviewOut,
                     call_id="rev-3", title="块审")
    assert len(tool.drives) == 2


def test_reviewer_does_not_retry_drive_failure():
    tool = _StubTaskTool([ToolException("Subagent 'case_review' failed: boom")])
    with pytest.raises(ToolException):
        run_reviewer(tool, "case_review", "简报", model_cls=ReviewOut,
                     call_id="rev-4", title="块审")
    assert len(tool.drives) == 1


@pytest.mark.parametrize("model_cls", [ReviewOut, EnumeratorOut, CompareOut, ClaimsOut, MatrixOut])
def test_reviewer_generic_over_all_review_models(model_cls):
    tool = _StubTaskTool([EMPTY_JSON])
    model, _ = run_reviewer(tool, "case_review", "简报", model_cls=model_cls,
                            call_id="rev-5", title="块审")
    assert isinstance(model, model_cls)
