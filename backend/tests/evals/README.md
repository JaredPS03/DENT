# Evaluaciones del bot

Son 84 conversaciones en `cases/*.yaml`, con la cobertura de `.claude/rules/bot-ia.md`. Cada caso corre el turno real del bot contra el consultorio de prueba de la base local, con Meta simulado y la API real de Claude.

- **Revisiones en código, en cada respuesta:**
  - qué herramientas llamó y cómo terminó el turno;
  - que nunca agende sin que el paciente elija;
  - que no pase de 3 botones;
  - que no aparezcan medicamentos ni diagnósticos, datos de otros pacientes ni piezas del prompt.
- **Rúbrica:** tono, claridad, brevedad, siguiente paso y alcance los califica `claude-sonnet-5-5` con `rubric.md`.
- **Al final** sale un resumen por categoría con el costo estimado. Urgencias, inyección y dudas clínicas no pueden fallar.

Cuestan dinero (menos de US$1 por corrida completa), por eso `uv run pytest` las excluye. Se corren a propósito, con la base local arriba, al cambiar el prompt, las herramientas o el orquestador y antes de cada despliegue:

```bash
ANTHROPIC_API_KEY=... uv run pytest tests/evals -m evals
```

Una sola: `... -m evals -k agendar-limpieza-texto`. Todo error real en producción se vuelve un caso nuevo.
