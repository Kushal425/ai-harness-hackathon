from pathlib import Path

from raven.learn.tune import successive_halving

TASKS_DIR = Path(__file__).parent.parent / "evals" / "tasks"


def test_successive_halving_completes_with_tiny_grid():
    task_paths = sorted(TASKS_DIR.glob("*.yaml"))[:4]
    tiny_grid = {"half_life": [2, 5]}
    result = successive_halving(task_paths, grid=tiny_grid)
    assert result.total == len(task_paths)
    assert result.best_knobs["half_life"] in (2, 5)
    assert result.best_resolved <= result.total
    assert result.all_results  # recorded intermediate rounds


def test_successive_halving_handles_single_combo():
    task_paths = sorted(TASKS_DIR.glob("*.yaml"))[:2]
    result = successive_halving(task_paths, grid={"half_life": [3]})
    assert result.best_knobs == {"half_life": 3}
