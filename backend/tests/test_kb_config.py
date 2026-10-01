from aitester.services.kb.config import KbConfig, build_reme_config


def test_bm25_only_without_api_key():
    cfg = build_reme_config(KbConfig(workspace_dir="ws", kb_id="demo"))
    assert cfg["knowledge_base_id"] == "demo"
    assert cfg["knowledge_bases_dir"] == ""
    assert cfg["create_knowledge_base"] is True
    assert cfg["service"]["web_enabled"] is False
    assert "as_embedding" not in cfg["components"]
    assert "embedding_store" not in cfg["components"]
    assert cfg["components"]["file_store"]["default"]["embedding_store"] == ""
    assert set(cfg["jobs"]) >= {
        "status", "list_knowledge_bases", "knowledge_search",
        "save_to_knowledge", "reindex", "list_knowledge_inbox",
        "promote_knowledge_inbox", "merge_knowledge_inbox", "reject_knowledge_inbox",
        "index_update_loop",
    }
    # spec 裁定4：后台索引环（index_update_loop），读侧各实例秒级收敛
    loop = cfg["jobs"]["index_update_loop"]
    assert loop["backend"] == "background"
    step_backends = [s["backend"] for s in loop["steps"]]
    assert step_backends == ["init_changes_step", "watch_changes_step"]


def test_embedding_injected_when_key_present():
    cfg = build_reme_config(KbConfig(
        workspace_dir="ws",
        embedding_api_key="sk-x",
        embedding_model="m-emb",
        embedding_dimensions=512,
    ))
    emb = cfg["components"]["as_embedding"]["default"]
    assert emb["backend"] == "openai"
    assert emb["model"] == "m-emb"
    assert emb["dimensions"] == 512
    assert emb["credential"] == {
        "api_key": "sk-x",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    assert cfg["components"]["embedding_store"]["default"]["as_embedding"] == "default"
    assert cfg["components"]["file_store"]["default"]["embedding_store"] == "default"
