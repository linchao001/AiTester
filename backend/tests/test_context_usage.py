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
    u.note_response(500, 10)                 # 这次请求带回了真值
    u.note_request(500)                      # 下一次估算与峰值相等
    assert u.peak_occupancy == 500
    assert u.occupancy_source == "actual"    # 平手时真值优先——估算不许盖掉真值
    assert u.rounds == 2                     # rounds 数的是请求次（brief 示例的 3 与其参考实现矛盾，见 task-C2-report）
    assert u.spent_input == 1000
    assert u.spent_output == 10


def test_fresh_accumulator_reads_zero_and_estimated_not_missing():
    """C6 的「—」显示口径：没样本不是坏，是 0 + estimated。"""
    u = ContextUsage(window=0)
    assert (u.rounds, u.peak_occupancy, u.occupancy_source) == (0, 0, "estimated")
    snap = u.snapshot()
    assert snap["window"] == 0 and snap["rounds"] == 0
    assert snap["truncated"] == [] and snap["error"] is None
    assert (snap["spent_input"], snap["spent_output"]) == (0, 0)


def test_truth_arriving_without_a_noted_request_still_counts_as_actual():
    """参考实现自留的分支：真值先到也要进账，且不许凭空造一轮。"""
    u = ContextUsage(window=1000)
    u.note_response(50, 7)
    assert u.rounds == 0                      # 补真值不增轮次
    assert (u.peak_occupancy, u.occupancy_source) == (50, "actual")
    assert (u.spent_input, u.spent_output) == (50, 7)


def test_truncations_accumulate_in_call_order_with_exact_keys():
    """C5/C6/C7 逐字断言这些键名与顺序——这里一次钉死。"""
    from aitester.context.usage import Truncation
    u = ContextUsage(window=1000)
    u.note_truncation(tool="shell", original=900, kept=300, dropped=600)
    u.note_truncation(tool="read", original=1000, kept=700, dropped=300)
    assert [t.tool for t in u.truncations] == ["shell", "read"]
    assert u.snapshot()["truncated"] == [
        {"tool": "shell", "original": 900, "kept": 300, "dropped": 600},
        {"tool": "read", "original": 1000, "kept": 700, "dropped": 300}]
    assert Truncation(tool="t", original=3, kept=2, dropped=1).to_dict() == {
        "tool": "t", "original": 3, "kept": 2, "dropped": 1}


def test_snapshot_is_a_fresh_copy_so_frame_and_disk_cannot_alias():
    """终帧与落盘各拿一份快照；改一份不许坏另一份（同源不同物）。"""
    u = ContextUsage(window=1000)
    u.note_request(10)
    first = u.snapshot()
    first["truncated"].append({"tool": "tampered", "original": 1, "kept": 1, "dropped": 0})
    assert u.snapshot()["truncated"] == []


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
