"""构建 ReMe Application 的纯 dict 配置（不加载 reme yaml）。

jobs/steps/components 结构与键名逐项对齐 reme 0.4.1.13 的
reme/config/default.yaml 与 reme/schema/application_config.py；
最小 dict 形态经 reme 仓库单测 tests/unit/test_knowledge_base.py 实证。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitester.case_design.constants import NODE_BUCKETS

DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"


@dataclass(frozen=True)
class KbConfig:
    workspace_dir: str
    kb_id: str = "zhb_kb"
    kb_bases_dir: str = ""
    create_missing: bool = True
    embedding_api_key: str = ""
    embedding_base_url: str = DEFAULT_EMBEDDING_BASE_URL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimensions: int = 1024


_KB_JOBS: dict[str, Any] = {
    "status": {"backend": "base", "steps": [{"backend": "status_step"}]},
    "list_knowledge_bases": {
        "backend": "base",
        "steps": [{"backend": "list_knowledge_bases_step"}],
    },
    "knowledge_base_meta": {
        "backend": "base",
        "steps": [{"backend": "knowledge_base_meta_step"}],
    },
    "knowledge_search": {
        "backend": "base",
        "steps": [{
            "backend": "knowledge_search_step",
            "vector_weight": 0.7,
            "candidate_multiplier": 5.0,
            "expand_links": True,
            "max_links_per_direction": 10,
        }],
    },
    "save_to_knowledge": {
        "backend": "base",
        "steps": [{"backend": "save_to_knowledge_step"}],
    },
    "reindex": {
        "backend": "base",
        "steps": [
            {"backend": "clear_store_step"},
            {
                "backend": "init_changes_step",
                "monitor_type": "file_store",
                "monitor_name": "default",
                "dispatch_steps": ["update_index_step"],
            },
        ],
    },
    # 一次性增量重扫（非 clear）：形态即 index_update_loop 首步抽出。
    # 名字必须是 index_sync——reme 只给白名单 job 追加发布桶绝对路径，
    # 别名会导致 init_changes 只扫空 knowledge 目录并谎报 up to date。
    "index_sync": {
        "backend": "base",
        "watch_dirs": ["knowledge"],
        "watch_suffixes": ["md"],
        "steps": [
            {
                "backend": "init_changes_step",
                "monitor_type": "file_store",
                "monitor_name": "default",
                "dispatch_steps": ["update_index_step"],
            },
        ],
    },
    # reme 内建后台索引环（spec 裁定4：读侧各实例经 watch 循环秒级收敛）。
    # 形态抄实装 reme/config/default.yaml 的 index_update_loop；
    # prepare_knowledge_startup/augment_jobs_for_knowledge 会在启动期把
    # knowledge/<bucket> 发布桶绝对路径追加进本 job 的 watch_dirs。
    "index_update_loop": {
        "backend": "background",
        # knowledge=共享 KB 整根（递归含节点桶）。个人记忆目录 daily/digest 在
        # build_reme_config 末尾按 workspace 绝对路径追加（避免仅靠 token 时
        # 目录尚未创建导致 watch 行为漂移）。
        "watch_dirs": ["knowledge"],
        "watch_suffixes": ["md"],
        "steps": [
            {
                "backend": "init_changes_step",
                "monitor_type": "file_store",
                "monitor_name": "default",
                "dispatch_steps": ["update_index_step"],
            },
            {
                "backend": "watch_changes_step",
                "dispatch_steps": [
                    {"backend": "update_index_step", "persist": False},
                ],
            },
        ],
    },
    "list_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "list_knowledge_inbox_step"}],
    },
    "promote_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "promote_knowledge_inbox_step"}],
    },
    "merge_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "merge_knowledge_inbox_step"}],
    },
    "reject_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "reject_knowledge_inbox_step"}],
    },
    # 三层节点桶（P-2）：op 由 step 配置注入；layer/node/id 走 job 调用 kwargs。
    "case_nodes_list": {
        "backend": "base",
        "steps": [{"backend": "aitester_kb_nodes_step", "op": "list"}],
    },
    "case_node_upsert": {
        "backend": "base",
        "steps": [{"backend": "aitester_kb_nodes_step", "op": "upsert"}],
    },
    "case_node_delete": {
        "backend": "base",
        "steps": [{"backend": "aitester_kb_nodes_step", "op": "delete"}],
    },
    # 个人记忆：workspace 日笔记 / digest 混合检索与回合后抽取
    "search": {
        "backend": "base",
        "steps": [{
            "backend": "search_step",
            "vector_weight": 0.7,
            "candidate_multiplier": 3.0,
            "expand_links": True,
            "max_links_per_direction": 10,
        }],
    },
    "auto_memory": {
        "backend": "base",
        "steps": [{"backend": "auto_memory_step"}],
    },
}


def build_reme_config(cfg: KbConfig) -> dict[str, Any]:
    components: dict[str, Any] = {
        "tokenizer": {"default": {"backend": "regex"}},
        "keyword_index": {"default": {"backend": "bm25", "tokenizer": "default"}},
        "file_graph": {"default": {"backend": "local"}},
        "file_catalog": {
            "default": {"backend": "local"},
            "digest": {"backend": "local"},
            "dream": {"backend": "local"},
        },
        # update_index_step 按扩展名解析 file_chunker；缺省即整条索引失败（键名/默认值对齐 default.yaml）。
        "file_chunker": {
            "markdown": {
                "backend": "markdown",
                "supported_extensions": ["md"],
                "embed_toc": True,
                "max_ast_sections": 100,
                "include_frontmatter_in_metadata": False,
                "include_frontmatter_keys_in_metadata": [],
            },
            "default": {
                "backend": "default",
                "supported_extensions": ["jsonl"],
            },
        },
        "file_store": {
            "default": {
                "backend": "local",
                "embedding_store": "",
                "keyword_index": "default",
                "file_graph": "default",
            },
        },
        # 运行时注入 AiTester LlmProvider；backend=langchain 走 Reme 0.4.1.13 适配层
        "as_llm": {
            "default": {
                "backend": "langchain",
                "model": "aitester-injected",
                "stream": True,
                "context_size": 200000,
                "max_retries": 3,
                "credential": {"api_key": "", "base_url": ""},
                "parameters": {"max_tokens": 8192},
            },
        },
        "agent_wrapper": {
            "default": {
                "backend": "agentscope",
                "as_llm": "default",
                "builtin_tools": False,
                "permission_mode": "bypass",
                "react_config": {"max_iters": 30},
                "context_config": {
                    "trigger_ratio": 0.8,
                    "reserve_ratio": 0.1,
                    "tool_result_limit": 50000,
                },
                "model_config": {"max_retries": 1},
            },
        },
    }
    if cfg.embedding_api_key:
        components["as_embedding"] = {
            "default": {
                "backend": "openai",
                "model": cfg.embedding_model,
                "dimensions": cfg.embedding_dimensions,
                "credential": {
                    "api_key": cfg.embedding_api_key,
                    "base_url": cfg.embedding_base_url,
                },
                "parameters": {},
            },
        }
        components["embedding_store"] = {
            "default": {"backend": "local", "as_embedding": "default"},
        }
        components["file_store"]["default"]["embedding_store"] = "default"

    jobs = copy.deepcopy(_KB_JOBS)
    # P-4：三层节点桶不在 reme 的 PUBLISHED_BUCKETS 里，启动期 augment_jobs_for_knowledge
    # 不会替我们追加；必须显式把 junction 侧绝对路径摆进 index/watch 相关 job。
    # index_sync / reindex / index_update_loop 都要挂：init_changes 只读本 job 的 watch_dirs。
    ws = Path(cfg.workspace_dir)
    extra_paths = [
        str(ws / "knowledge" / bucket) for bucket in NODE_BUCKETS
    ] + [str(ws / "daily"), str(ws / "digest")]
    for job_name in ("index_update_loop", "reindex", "index_sync"):
        job = jobs.get(job_name)
        if not isinstance(job, dict):
            continue
        watch_dirs = list(job.get("watch_dirs") or [])
        for path in extra_paths:
            if path not in watch_dirs:
                watch_dirs.append(path)
        job["watch_dirs"] = watch_dirs

    return {
        "enable_logo": False,
        "log_to_file": False,
        "log_to_console": False,
        "workspace_dir": cfg.workspace_dir,
        "knowledge_bases_dir": cfg.kb_bases_dir,
        "knowledge_base_id": cfg.kb_id,
        "knowledge_dir": "knowledge",
        "create_knowledge_base": cfg.create_missing,
        "knowledge_write_mode": "open",
        # reme 插件机制：entry point aitester → aitester.kb_plugin/plugin.yaml 注册节点 step。
        # entry point 元数据缺失（未重装）时 Application 构造响亮失败——重装是运行前提。
        "plugins": ["aitester"],
        "service": {"backend": "http", "web_enabled": False, "port": 8199},
        "components": components,
        "jobs": jobs,
    }
