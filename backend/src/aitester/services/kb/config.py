"""构建 ReMe Application 的纯 dict 配置（不加载 reme yaml）。

jobs/steps/components 结构与键名逐项对齐 reme 0.4.1.8 的
reme/config/default.yaml 与 reme/schema/application_config.py；
最小 dict 形态经 reme 仓库单测 tests/unit/test_knowledge_base.py 实证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
}


def build_reme_config(cfg: KbConfig) -> dict[str, Any]:
    components: dict[str, Any] = {
        "tokenizer": {"default": {"backend": "regex"}},
        "keyword_index": {"default": {"backend": "bm25", "tokenizer": "default"}},
        "file_graph": {"default": {"backend": "local"}},
        "file_store": {
            "default": {
                "backend": "local",
                "embedding_store": "",
                "keyword_index": "default",
                "file_graph": "default",
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
        "service": {"backend": "http", "web_enabled": False, "port": 8199},
        "components": components,
        "jobs": {name: dict(spec) for name, spec in _KB_JOBS.items()},
    }
