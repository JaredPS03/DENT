"""Summary of the evaluations by category, after the run."""

from collections import defaultdict
from typing import Any

from tests.evals.report import RESULTS
from tests.evals.runner import CRITICAL
from tests.integration.conftest import database_url  # noqa: F401 - the local database fixture


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if not RESULTS:
        return
    by_category: dict[str, list[Any]] = defaultdict(list)
    for result in RESULTS:
        by_category[result.case.category].append(result)
    write = terminalreporter.write_line
    write("")
    write("Evaluaciones del bot por categoría")
    for category, results in sorted(by_category.items()):
        passed = sum(result.passed for result in results)
        mark = " (crítica: no puede fallar)" if category in CRITICAL else ""
        write(f"  {category}: {passed}/{len(results)}{mark}")
    total = sum(result.passed for result in RESULTS)
    write(f"  Total: {total}/{len(RESULTS)}")
    scored = [result.scores for result in RESULTS if result.scores]
    if scored:
        for name in ("tono", "claridad", "brevedad", "siguiente_paso", "alcance"):
            average = sum(score[name] for score in scored) / len(scored)
            write(f"  Rúbrica {name}: {average:.2f} de 5 ({len(scored)} casos)")
    bot = sum(result.bot_microusd for result in RESULTS) / 1_000_000
    judge = sum(result.judge_microusd for result in RESULTS) / 1_000_000
    write(f"  Costo estimado: bot US${bot:.4f}, juez US${judge:.4f}")
    for result in RESULTS:
        for failure in result.failures:
            write(f"  FALLA {result.case.id}: {failure}")
