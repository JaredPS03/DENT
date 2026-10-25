# Hooks de Claude Code propuestos

Propuesta de la Fase 0, **sin configurar**. Los hooks corren comandos de forma automática en tu computadora, así que actívalos tú cuando los revises: se agregan en `.claude/settings.json` (para todo el equipo) o en `.claude/settings.local.json` (solo para ti). Están ordenados por cuánto valen.

## 1. Bloquear la edición de migraciones ya aplicadas (regla 9)

**Qué evita:** que Claude edite una migración que ya corrió en staging o producción. La base de la nube no se enteraría del cambio y el repo diría otra cosa que la base.

**Cómo:** un hook `PreToolUse` sobre `Edit|Write` que reciba la ruta del archivo y, si está en `supabase/migrations/` y ese archivo ya existe en la rama principal publicada (`origin/main` cuando exista; hoy el trabajo vive en ramas `claude/…`), responda con código 2 y el mensaje "Crea una migración nueva con `pnpm supabase migration new <nombre>`".

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "uv run --project backend python scripts/hooks/proteger_migraciones.py" }]
      }
    ]
  }
}
```

El script lee el JSON del hook por la entrada estándar, toma `tool_input.file_path` y revisa con `git cat-file -e origin/main:<ruta>`. Así sigue funcionando en Windows.

## 2. Formatear al editar

**Qué evita:** diffs con ruido de formato y CI rojo por `ruff format --check`.

**Cómo:** un hook `PostToolUse` sobre `Edit|Write`:
- Si el archivo es `backend/**/*.py`: `uv run ruff format <archivo>` y `uv run ruff check --fix <archivo>`.
- Si es `panel/**/*.{ts,tsx}`: `pnpm --filter panel exec eslint --fix <archivo>`.

Solo toca el archivo editado, así que es rápido.

## 3. Bloquear la escritura de `.env` y llaves

**Qué evita:** que un secreto termine en un archivo que Claude creó. `settings.json` ya le impide **leer** `.env`; esto le impide **escribirlo**.

**Cómo:** un `PreToolUse` sobre `Edit|Write` que rechace rutas que terminen en `.env`, `.env.*` (salvo `.env.example`), `.pem` o `.key`.

## 4. Recordar la bitácora al cerrar

**Qué evita:** cerrar un hito sin dejar `docs/avance.md` al día, que es la memoria entre sesiones.

**Cómo:** un hook `Stop` que, si hay cambios en `backend/`, `supabase/` o `panel/` y `docs/avance.md` no cambió en la rama, imprima un recordatorio. Solo avisa; no bloquea.

## 5. Bloquear comandos contra la base de la nube

**Qué evita:** que Claude resetee la base de la nube o le aplique migraciones. Las reglas de `settings.json` comparan el texto del comando con patrones, y siempre hay otra forma de escribirlo: `pnpm run`, banderas antes del subcomando, `node_modules/.bin/supabase`. Importa desde el hito 1F, cuando enlaces staging.

**Cómo:** un hook `PreToolUse` sobre `Bash` que lea `tool_input.command` y responda con código 2 cuando el comando junte las dos cosas:

- una operación sobre la base: `db reset`, `db:reset`, `db push`, `migration up` o `migration repair`;
- un destino en la nube: `--linked`, `--db-url`, `--project-ref` o `SUPABASE_YES`.

El mensaje: "Las migraciones a la nube se aplican desde CI".

Y que pida confirmación (`"permissionDecision": "ask"`) ante los comandos que gestionan el proyecto de la nube, sin importar las banderas:

- `config push`: mandaría a producción la configuración local de Auth;
- `projects delete`, `backups restore` y `storage rm`;
- `db dump`: exporta a tu computadora datos de pacientes.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "uv run --project backend python scripts/hooks/bloquear_nube.py" }]
      }
    ]
  }
}
```

No reemplaza lo importante: aplicar las migraciones desde CI con aprobación y no dejar la computadora enlazada a la nube (`supabase logout`) mientras Claude trabaja en modo bypass.

## Que no recomiendo

- **Correr todas las pruebas en cada edición:** pgTAP y la integración tardan; mejor al cerrar cada tarea, como ya pide `CLAUDE.md`.
- **Correr las evaluaciones del bot automáticamente:** llaman a la API real y cuestan dinero.
