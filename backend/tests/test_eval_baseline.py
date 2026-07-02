from pathlib import Path

from app.eval.baseline import load_baseline, regressions, save_baseline


def test_load_absent_returns_none(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "nope.json") is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    save_baseline({"faithfulness": 0.9, "execution_accuracy": 1.0}, path)
    assert load_baseline(path) == {"faithfulness": 0.9, "execution_accuracy": 1.0}


def test_no_baseline_means_no_regression() -> None:
    assert regressions(None, {"faithfulness": 0.1}, tolerance=0.05) == []


def test_within_tolerance_ok() -> None:
    base = {"faithfulness": 0.90}
    assert regressions(base, {"faithfulness": 0.87}, tolerance=0.05) == []


def test_drop_beyond_tolerance_flags() -> None:
    base = {"faithfulness": 0.90}
    out = regressions(base, {"faithfulness": 0.80}, tolerance=0.05)
    assert len(out) == 1 and "faithfulness" in out[0]


def test_improvement_is_not_regression() -> None:
    base = {"faithfulness": 0.80}
    assert regressions(base, {"faithfulness": 0.95}, tolerance=0.05) == []


def test_missing_current_key_counts_as_zero() -> None:
    base = {"execution_accuracy": 1.0}
    out = regressions(base, {}, tolerance=0.05)
    assert len(out) == 1 and "execution_accuracy" in out[0]
