"""在途 run 注册表：命中/未命中/结束后取消，三条语义。"""

from aitester.services.run_registry import RunRegistry, new_run_id


def test_start_then_cancel_hits_the_control() -> None:
    runs = RunRegistry()
    rid = new_run_id()
    control = runs.start(rid)
    assert runs.cancel(rid) is True
    assert control.cancelled is True          # 取消位落在同一片内存上，不是副本


def test_cancel_unknown_or_finished_returns_false() -> None:
    runs = RunRegistry()
    rid = new_run_id()
    runs.start(rid)
    runs.finish(rid)
    assert runs.cancel(rid) is False          # 已结束的回答：路由据此回 404
    assert runs.cancel(new_run_id()) is False


def test_run_ids_are_unique_and_runs_are_independent() -> None:
    runs = RunRegistry()
    a, b = new_run_id(), new_run_id()
    assert a != b
    ca, cb = runs.start(a), runs.start(b)
    runs.cancel(a)
    assert ca.cancelled is True
    assert cb.cancelled is False              # 停一条不能波及另一条
