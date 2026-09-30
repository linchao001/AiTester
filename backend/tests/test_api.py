from fastapi.testclient import TestClient

from aitester.main import app, create_app

client = TestClient(app)

ALL_LAYERS = [
    "interaction",
    "services",
    "context",
    "orchestration",
    "adapters",
    "memory",
    "storage",
]


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "aitester-backend"}


def test_chat_echo_traverses_all_seven_layers() -> None:
    resp = client.post("/api/chat/echo", json={"session_id": "s1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] hello"
    assert body["trace"] == ALL_LAYERS


def test_chat_echo_rejects_empty_message() -> None:
    resp = client.post("/api/chat/echo", json={"message": ""})
    assert resp.status_code == 422


def test_chat_service_is_per_app_instance() -> None:
    app_a, app_b = create_app(), create_app()
    client_a, client_b = TestClient(app_a), TestClient(app_b)
    resp = client_a.post("/api/chat/echo", json={"session_id": "iso", "message": "hello"})
    assert resp.status_code == 200
    assert client_b.post("/api/chat/echo", json={"session_id": "other", "message": "hi"})
    assert app_a.state.chat_service.memory.recall("iso")
    assert app_b.state.chat_service.memory.recall("iso") == []
