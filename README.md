# Axon Dental

Asistente de WhatsApp con IA para consultorios dentales pequeños en México: contesta, agenda, recuerda citas y le demuestra al dentista cuánto dinero le recupera cada mes.

- Qué se construye y por qué: `docs/especificacion.md` (fuente de verdad).
- Dónde va el proyecto: `docs/avance.md`.
- Por qué es como es: `docs/decisiones/`.
- Cómo trabaja Claude Code aquí: `CLAUDE.md`, `.claude/` y `LEEME.md`.

## Estructura

```text
backend/    Python 3.12 + FastAPI (uv): webhooks, /api y worker
panel/      Next.js + Tailwind: el panel del consultorio (PWA)
supabase/   migraciones, pruebas pgTAP y seed local
docs/       especificación, avance, decisiones y prompt del bot
prompts/    prompts de arranque por fase
```

## Requisitos

- **Docker Desktop.** En Windows, con el backend WSL2. Supabase local corre en Docker.
- **Node.js 20.9 o más y pnpm 10** (`corepack enable` lo deja listo).
- **uv**, que instala Python 3.12 solo.
- **Git.**

En Windows todo funciona desde PowerShell, pero lo más estable es clonar y trabajar dentro de WSL2 (Ubuntu): Docker, uv y pnpm corren ahí sin diferencias con el CI.

## Primeros pasos

```bash
pnpm install                 # instala la CLI de Supabase (fijada) y el panel
pnpm db:start                # levanta Supabase local: migraciones + seed de dos consultorios ficticios
pnpm db:test                 # pruebas pgTAP de aislamiento entre consultorios

cd backend
cp .env.example .env         # y llena los valores de Meta (en Windows: copy .env.example .env)
uv sync
uv run pytest                # unitarias + integración contra la base local
uv run fastapi dev app/main.py
```

En otra terminal, para el panel: `pnpm --filter panel dev` (http://localhost:3000).

## Comandos

| Qué | Dónde | Comando |
| --- | --- | --- |
| Levantar / apagar Supabase local | raíz | `pnpm db:start` / `pnpm db:stop` |
| Base desde cero (migraciones + seed) | raíz | `pnpm db:reset` |
| Pruebas de la base (pgTAP) | raíz | `pnpm db:test` |
| Pruebas del backend | `backend/` | `uv run pytest` |
| Lint, formato y tipos del backend | `backend/` | `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests` |
| Backend en desarrollo | `backend/` | `uv run fastapi dev app/main.py` |
| Mensaje de prueba de WhatsApp | `backend/` | `uv run python -m app.cli send-test-message --to +52...` |
| Panel: lint, tipos y build | raíz | `pnpm panel:check` |

## Seguridad

- Ningún `.env` se sube al repositorio. Los secretos van en los secretos de GitHub y del hosting.
- Nunca uses datos reales de pacientes en desarrollo; el seed trae datos ficticios.
- Fuera de producción solo se envían mensajes a los números de `WHATSAPP_ALLOWED_TEST_NUMBERS`.
