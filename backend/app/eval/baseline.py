import json
from pathlib import Path

BASELINE_PATH = Path(__file__).with_name("baseline.json")


def load_baseline(path: Path | None = None) -> dict[str, float] | None:
    path = path or BASELINE_PATH
    if not path.exists():
        return None
    data: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_baseline(metrics: dict[str, float], path: Path | None = None) -> None:
    path = path or BASELINE_PATH
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def regressions(
    baseline: dict[str, float] | None, current: dict[str, float], tolerance: float
) -> list[str]:
    if baseline is None:
        return []
    out: list[str] = []
    for key, base in baseline.items():
        cur = current.get(key, 0.0)
        if cur < base - tolerance:
            out.append(f"{key}: {cur:.3f} < baseline {base:.3f} - tol {tolerance:.3f}")
    return out
