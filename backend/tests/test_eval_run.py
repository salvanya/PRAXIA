from app.eval.run import gate_exit_code


def test_exit_zero_when_clean() -> None:
    assert gate_exit_code(0, []) == 0


def test_exit_one_on_hard_failure() -> None:
    assert gate_exit_code(1, []) == 1


def test_exit_one_on_regression() -> None:
    assert gate_exit_code(0, ["faithfulness: 0.8 < baseline 0.9"]) == 1
