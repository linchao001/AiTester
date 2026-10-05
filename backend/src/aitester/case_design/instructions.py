"""阶段指令：驱动下发给主智能体的 HumanMessage 模板（中文；零产品线名词）。"""

from __future__ import annotations

import json

from aitester.case_design.constants import LAYER_CN
from aitester.case_design.schema import DraftNode

_MODE_CN = {"first_build": "首建", "update": "更新"}


def _schema_text() -> str:
    return json.dumps(DraftNode.model_json_schema(), ensure_ascii=False, indent=1)


def plan_instruction() -> str:
    return (
        "【编排·计划】开始一次测试设计任务。请只读地弄清两件事，然后只写一个文件 design/plan.json：\n"
        "1) 业务信息来源：从上面的用户指令判断本次依据哪些项目内业务文档（需求说明、接口文档等），"
        "逐个确认存在后列入 source_files（项目相对路径）；不要列 design/ 工作稿，"
        "也不要列知识库里的文件（知识库业务资料由编排层另行装载）。\n"
        "2) 范围归一化：把指令拆成「入口层 / 目标子树 / 终止层」——task_kind 取 "
        "design（测试设计）/ mixed（设计+用例混杂，本期只做设计侧）/ case_only（纯用例任务，本期只做设计部分）；"
        "entry_layer 与 terminal_layer 取 chain|story|point；「只针对某条链路/某棵子树」这类窄指令把 "
        "target_subtree 填成对应节点 id，全量任务留空串。\n"
        'JSON 形状：{"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point", '
        '"target_subtree": "", "source_files": ["docs/xx.md"], "note": "一句话"}\n'
        "只输出这个文件，不要改动其他任何文件。写完即停。"
    )


def gen_instruction(layer: str, block: str, *, draft_path: str, ref_hint: str,
                    kb_manifest_path: str | None = None, opinions_path: str | None = None,
                    errors: list[str] | None = None, mode: str = "first_build") -> str:
    # 更新态必须给既有节点清单路径：否则会把字面 None 写进模型可读的指令里，
    # 诱导生成阶段做一次幻觉读文件（付费轮次）并落进待校验草稿。属编程错误，直接响亮失败。
    if mode == "update" and not kb_manifest_path:
        raise ValueError("mode=update 需要 kb_manifest_path")
    lines = [f"【编排·生成·{LAYER_CN[layer]}·块 {block}】（{_MODE_CN.get(mode, mode)}）",
             f"产出本块草稿并写入 {draft_path}（只写这一个文件）。"]
    if mode == "update":
        lines.append(f"先按需精读既有节点清单 {kb_manifest_path}（只读本块涉及的节点，不要通读全量），"
                     "新增 / 修改 / 删除都以本块草稿表达。")
    if opinions_path:
        lines.append(f"本块在上一轮收到意见，清单见 {opinions_path}：生成时直接消化。")
    lines += [
        f"引用提示：{ref_hint}",
        "草稿文件形状：{\"layer\": \"" + layer + "\", \"block\": \"" + block + "\", \"nodes\": [...]}",
        "节点 schema（JSON Schema，仅生成时参考）：",
        "```json",
        _schema_text(),
        "```",
        "约束：新增节点 id 留空串（由编排层分配）；引用其他节点（parent/chains/story）必须填其既有 id；"
        "只写本块涉及的节点，不复述全量；删除节点用 {\"op\":\"delete\",\"type\":\"...\",\"id\":\"...\",\"reason\":\"...\"}。",
    ]
    if errors:
        lines.append("上一版未通过校验，请修正后重写整个文件：")
        lines += [f"- {e}" for e in errors]
    lines.append("写完即停。")
    return "\n".join(lines)


def opt_instruction(layer: str, block: str, round_no: int, *, draft_path: str,
                    opinions_path: str, fix_path: str, source_cn: str) -> str:
    return "\n".join([
        f"【编排·优化·{LAYER_CN[layer]}·块 {block}·第 {round_no} 轮】（{source_cn}意见）",
        f"意见清单（含编号）在 {opinions_path}。逐条消化：",
        f"- 需要改的：直接改进 {draft_path}（或新增 / 删除节点）。",
        "- 复核确认本版已覆盖的：不算未消化，但要写进处置表。",
        f"产出两件：① 更新后的 {draft_path}；② 处置表 {fix_path}，形状：",
        '{"dispositions": [{"ref": "op-01", "status": "fixed|covered|unresolved", '
        '"note": "改了什么 / 为何已覆盖 / 为何仍未消化"}]}',
        "每条意见都必须有去向，不允许静默忽略；unresolved 只能用于你判定无法在本轮消化的意见并说明理由。",
        "写完即停。",
    ])


def attribute_instruction(layer: str, block: str, *, opinions_path: str, out_path: str) -> str:
    return "\n".join([
        f"【编排·轮次用尽·{LAYER_CN[layer]}·块 {block}】评审轮次已达上限，仍有未消化意见（清单在 {opinions_path}）。",
        f"请给出不收敛归因，写入 {out_path}：",
        '{"cause": "业务信息不足|契约冲突|评审分歧|成本超限", "note": "一句说明"}',
        "写完即停。",
    ])
