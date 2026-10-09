"""阶段指令：驱动下发给主智能体的 HumanMessage 模板（中文；零产品线名词）。"""

from __future__ import annotations

import json

from aitester.case_design.constants import LAYER_CN
from aitester.case_design.schema import DraftNode

_MODE_CN = {"first_build": "首建", "update": "更新"}


def _schema_text() -> str:
    return json.dumps(DraftNode.model_json_schema(), ensure_ascii=False, indent=1)


def chat_instruction() -> str:
    """明确闲聊：无工具面，只回一句话。"""
    return (
        "【编排·闲聊】用户这条消息没有布置测试设计或用例编写任务。"
        "禁止调用任何工具（含读写文件、知识库检索、委派子智能体）。"
        "用一两句话自然回应用户；可简短说明你能做测试设计与用例编写，"
        "请对方明确提出设计或编写任务后再开始。"
        "不要提到本条指令。"
    )


def intent_instruction(errors: list[str] | None = None) -> str:
    lines = [
        "【编排·意向】用户这句话不够明确。开始之前先做一次判定：是不是在发起一次"
        "测试设计或用例编写任务。把判定只写进一个文件 design/intent.json，不要做别的。",
        "判定口径：只有用户明确在布置或推进测试设计 / 用例编写工作时才是 task"
        "（例如生成或更新测试设计、编写用例、拆链路 / 故事 / 测试点、提出范围与要求）；"
        "打招呼、闲聊、问你是谁或能做什么、泛泛请教、没有明确任务动词的，一律 chat。"
        "拿不准时偏 chat——不要为了「好像在问测试」就开 task。",
        '文件形状：{"intent": "task"} 或 {"intent": "chat"}',
        "本步工具面只有 write：禁止检索知识库、禁止读项目文件、禁止委派、禁止改其他文件。",
        "若判定为 task：只写这个文件，不要展开设计工作。",
        "若判定为 chat：先写这个文件，写完后用一两句话照常回应用户"
        "（不要提这个判定文件，也不要提到本条指令）。",
    ]
    if errors:
        lines.append("上一次判定没有生效，请修正后重写整个文件：")
        lines += [f"- {e}" for e in errors]
    lines.append("写完即停。")
    return "\n".join(lines)


def plan_instruction() -> str:
    return (
        "【编排·计划】开始一次测试设计任务。请只读地弄清两件事，然后只写一个文件 design/plan.json：\n"
        "1) 业务信息来源：从上面的用户指令判断本次依据哪些项目内业务文档（需求说明、接口文档等），"
        "逐个确认存在后列入 source_files（项目相对路径）；不要列 design/ 工作稿，"
        "也不要列知识库里的文件（知识库业务资料由编排层另行装载）。\n"
        "2) 范围归一化：把指令拆成「入口层 / 目标子树 / 终止层」——task_kind 取 "
        "design（测试设计）/ mixed（设计+用例，先做设计侧、过门回写后同轮续编写环）/ "
        "case_only（纯用例任务：三层只读当分母，本 run 不改三层，只产 design/cases/ 里的用例正文）；"
        "entry_layer 与 terminal_layer 取 chain|story|point；target_subtree 只接受链路（ch-）id——"
        "「只针对某条链路」这类窄指令填该链路 id，指向用户故事或测试点时"
        "填其所属链路 id，全量任务留空串。\n"
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
        lines.append(
            "第四种合法去向·本块无变化：逐条比对后确认本次业务信息没动到本块（既不必新增也不必改删），"
            '就把草稿整个写成 {"layer": "' + layer + '", "block": "' + block + '", "nodes": [], '
            '"note": "no_change", "reason": "一句话说明为什么本块无变化"}。'
            "note 必须逐字是 no_change，nodes 必须是空数组，理由只写在 reason——"
            "这条通道只属于更新模式：本层 KB 无维护（首建）时无变化根本不存在。")
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


def gate_fix_instruction(*, issues_path: str, round_no: int) -> str:
    return "\n".join([
        f"【编排·大纲门修复·第 {round_no} 轮】④ 确定性检查发现结构问题（清单在 {issues_path}）。",
        "请逐条修复对应的层草稿（design/drafts/ 下），只改被点名的文件：",
        "- broken_parent：引用（parent/chains/story）必须指向引用宇宙内存在的节点 id。",
        "- cross_level：父子 level 必须连续（子 = 父 + 1）。",
        "- priority_violation：子节点优先级不得**高于**其父（重要性 P0 > P1 > P2；父比子更重要是正常降级，子比父更重要才是违例——要么父该提级，要么子该降级）。",
        "- unapproved_ref：被引用的节点必须已过审；失效层节点不得被引用。",
        "- empty_chain / empty_story：范围内链路必须有故事认领、范围内故事必须有测试点（在对应层草稿补节点）。",
        "不要做与问题无关的改动；修完即停，编排层会重新检查。",
    ])


def case_gen_instruction(*, chain: str, batch: str, manifest_path: str,
                         points: list[str], batch_no: int, batch_total: int) -> str:
    """第四层批生成指令：一个批次一文件、逐点认领，纪律写死在指令里（裁定 37/38）。"""
    return (
        f"【编排·用例批次生成·链路 {chain}·批次 {batch}·第 {batch_no}/{batch_total} 批】\n"
        f"1) 先读分母清单 {manifest_path}：每个点自带 scenario/entities/directions 与所属故事的 "
        f"actor/preconditions/trigger/expected，写用例所需信息全在里面，不必再翻知识库。\n"
        f"2) 用例正文落 design/cases/{batch}.json，根对象与每条用例的字段逐字如下（多余的键不要加）：\n"
        '   {"chain": "' + chain + '", "batch": "' + batch + '", "cases": ['
        '{"case_id": "", "title": "…", "covers": ["pt-0000"], "preconditions": "…", '
        '"steps": ["…"], "expected": ["…"], "priority": "P1", "note": ""}]}\n'
        f"3) 认领纪律：本批必须落实的点 = {'、'.join(points)}。每个点至少被一条用例的 covers 点名；"
        "covers 只许填这些 pt-四位数字 id，填界外的点会被判假完整。\n"
        "4) 点与用例数量不固定：一个点可拆多条（换账号/换数据），多个点也可合一条。"
        "禁止为凑数写无用例，也禁止拿「用例数 ≥ 点数」自证完整——核对只看 covers。\n"
        "5) 正文纪律：steps 每步是可执行动作（不写「进行操作」这类空话）；expected 是硬断言"
        "（可核对的具体结果，禁止「正常」「正确」「符合预期」「没有问题」「成功即可」）；"
        "preconditions 只写本用例自己造的前置，禁止拿环境存量当条件"
        "（「已有」「已存在」「存量数据」「库中已有」「环境中已」这类写法一律拒收）；"
        "case_id 留空串，由系统分配，priority 取 P0/P1/P2。\n"
        "6) 写完只回一句确认，不要复述用例正文。"
    )


def case_opt_instruction(chain: str, batch: str, round_no: int, *, cases_path: str,
                         opinions_path: str, fix_path: str) -> str:
    return "\n".join([
        f"【编排·用例优化·链路 {chain}·批次 {batch}·第 {round_no} 轮】（批评审意见）",
        f"意见清单（含编号）在 {opinions_path}。逐条消化：",
        f"- 需要改的：直接改进 {cases_path}（改正文、拆条、合并、删掉无用例都行，covers 要跟着改准）。",
        "- 复核确认本版已覆盖的：不算未消化，但要写进处置表。",
        "- 新增用例的 case_id 留空串（由编排层分配）；covers 只许填本批分母清单里的 pt- 四位数字 id。",
        f"产出两件：① 更新后的 {cases_path}；② 处置表 {fix_path}，形状：",
        '{"dispositions": [{"ref": "op-01", "status": "fixed|covered|unresolved", '
        '"note": "改了什么 / 为何已覆盖 / 为何仍未消化"}]}',
        "每条意见都必须有去向，不允许静默忽略；unresolved 只能用于你判定无法在本轮消化的意见并说明理由。",
        "本批每个测试点都必须仍有用例认领：删用例前先把它认领的点交给别的用例，否则会被判漏测。",
        "写完即停。",
    ])


def case_attribute_instruction(batch: str, *, opinions_path: str, out_path: str) -> str:
    return "\n".join([
        f"【编排·用例轮次用尽·批次 {batch}】评审轮次已达上限，仍有未消化意见（清单在 {opinions_path}）。",
        f"请给出不收敛归因，写入 {out_path}：",
        '{"cause": "业务信息不足|契约冲突|评审分歧|成本超限", "note": "一句说明"}',
        "cause 只能取上面四个值之一，note 不得为空。",
        "写完即停。",
    ])


def case_gate_fix_instruction(*, issues_path: str, round_no: int) -> str:
    """末门修复指令：与 `gate_fix_instruction` 同纪律——每个 code 的**改法**逐字写出来，
    不写「请自行修复」（主智能体只能按清单动手）。"""
    return "\n".join([
        f"【编排·用例末门修复·第 {round_no} 轮】末门确定性核对发现履约问题（清单在 {issues_path}）。",
        "请只改被点名的批次正文（design/cases/<批次 id>.json），逐条清零：",
        "- uncovered_point：该测试点没有任何用例认领——补一条认领它的用例，"
        "或把它并进已有用例的 covers（并进后那条用例正文必须真的覆盖它）。",
        "- phantom_cover：用例认领了分母外的点——covers 只许填本批清单里真实存在的 pt- 四位数字 id，"
        "点 id 写错就改对，越界的用例直接删掉。",
        "- 批次正文不可解析：按形状重写该文件（cases 数组，每条含 title/covers/preconditions/"
        "steps/expected/priority，新增用例 case_id 留空串）。",
        "问题清单里的 batch 字段就是该改的文件；不要做与清单无关的改动，"
        "也不要改测试点分母（那是设计侧的事）。修完即停，编排层会重新核对。",
    ])
