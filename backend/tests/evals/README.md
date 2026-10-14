# Evaluaciones del bot

Llegan en el hito 1C: al menos 50 conversaciones en YAML con la cobertura de `.claude/rules/bot-ia.md`.
Llaman a la API real de Claude y cuestan dinero, por eso `uv run pytest` las excluye con el marcador `evals`.
Se corren a propósito: `uv run pytest tests/evals -m evals`.
