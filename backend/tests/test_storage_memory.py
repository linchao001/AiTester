from aitester.memory import InMemoryMemoryStore
from aitester.storage import InMemoryRepository


def test_repository_put_get() -> None:
    repo = InMemoryRepository()
    repo.put("session:s1", {"session_id": "s1", "last_reply": "hi"})
    assert repo.get("session:s1") == {"session_id": "s1", "last_reply": "hi"}
    assert repo.get("session:missing") is None


def test_memory_store_save_recall() -> None:
    store = InMemoryMemoryStore()
    assert store.recall("s1") == []
    store.save("s1", "user", "你好")
    store.save("s1", "assistant", "[mock] 你好")
    assert store.recall("s1") == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "[mock] 你好"},
    ]
    assert store.recall("s2") == []
