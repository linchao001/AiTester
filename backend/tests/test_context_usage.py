from aitester.context.usage import ContextUsage


def test_request_then_truth_replaces_that_rounds_sample():
    u = ContextUsage(window=131_072)
    u.note_request(100)
    assert (u.peak_occupancy, u.occupancy_source) == (100, "estimated")
    u.note_response(80, 5)
    assert u.rounds == 1
    assert (u.peak_occupancy, u.occupancy_source) == (80, "actual")
    assert (u.spent_input, u.spent_output) == (80, 5)


def test_missing_truth_stays_estimated_and_is_never_written_as_truth():
    u = ContextUsage(window=1000)
    u.note_request(60)
    u.note_response(None, None)              # provider 没回 usage
    assert u.occupancy_source == "estimated" # R-C1：不许把 60 伪装成真值
    assert u.peak_occupancy == 60
    assert u.snapshot()["error"] is None      # 缺真值不是错误


def test_peak_is_max_over_rounds_and_actual_wins_ties():
    u = ContextUsage(window=1000)
    u.note_request(100)
    u.note_response(500, 10)                 # 第二轮真值更大
    u.note_request(500)                      # 第三轮估算与之相等
    assert u.peak_occupancy == 500
    assert u.occupancy_source == "actual"    # 平手时真值优先——估算不许盖掉真值
    assert u.rounds == 2                       # 两次 note_request ⇒ 两轮；brief 示例写 3，与其自身参考实现及本例 spent_input==1000 矛盾（见 task-C2-report）
    assert u.spent_input == 1000


def test_snapshot_keys_are_exact():
    u = ContextUsage(window=1000)
    u.note_request(10)
    u.note_truncation(tool="shell", original=900, kept=300, dropped=600)
    snap = u.snapshot()
    assert set(snap) == {"window", "rounds", "peak_occupancy", "occupancy_source",
                         "spent_input", "spent_output", "truncated", "error"}
    assert snap["truncated"] == [{"tool": "shell", "original": 900,
                                  "kept": 300, "dropped": 600}]


def test_error_is_recorded_verbatim_and_keeps_other_readings():
    u = ContextUsage(window=1000)
    u.note_request(42)
    u.error = "cap 失败：ValueError"
    assert u.snapshot()["error"] == "cap 失败：ValueError"
    assert u.snapshot()["peak_occupancy"] == 42      # 一处坏不抹掉其余读数
