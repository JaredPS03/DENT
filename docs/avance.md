# Avance

Bitácora del proyecto. Claude la actualiza al cerrar cada hito y la lee al empezar el siguiente; tú la lees para saber dónde va todo. Lo más reciente va arriba.

## Estado actual

- **Fase y hito:** Fase 0 · Cimientos. El código está terminado y pasó la revisión de cierre. Falta la puerta: mandar y recibir un mensaje con el número propio de Axon, y `pnpm db:test` en verde en tu computadora.
- **Siguiente paso:** Jared hace la lista de "Lo haces tú" de abajo, ensaya con el número de prueba de Meta y pasa la puerta con el número de Axon siguiendo `docs/puerta-fase-0.md`. Después, `/clear` y el prompt del hito 1A de `prompts/fases.md`.
- **Bloqueos:** la puerta necesita la app de Meta y el número propio de Axon dado de alta en la API; solo Jared puede tramitarlos.

## Plantilla de cada entrada

```markdown
### AAAA-MM-DD · Hito 1A · Agenda y disponibilidad
- **Qué quedó:** lo que ya funciona, en una o dos líneas.
- **Evidencia:** comandos corridos y su resultado (pruebas, evaluaciones, revisores).
- **Decisiones:** enlaces a docs/decisiones/.
- **Pendiente:** lo que falta o quedó fuera, y por qué.
```

## Entradas

### 2026-10-23 · Fase 0 · Revisión de cierre

- **Qué quedó:**
  - El seed es una sola transacción: aplicado a la nube con cualquier cliente, psql incluido, no aplica nada.
  - `DATABASE_URL` se lee con el parser de libpq. Si está mal formada, el backend no arranca y lo dice sin citarla. El error de arranque solo muestra textos propios.
  - asyncio ya no escribe en los logs el `repr` de tareas y excepciones. `within` limpia el trabajo abandonado en sus dos salidas.
  - `.gitignore` ignora las copias de `.env`, y `.claude/settings.json` usa comodines contra los comandos que tocan la base de la nube.
  - La guía de la puerta pide el número propio de Axon y `pnpm db:test`. El número de prueba de Meta queda como ensayo, y la guía advierte no usar el número de tu prima.
  - Parches de seguridad publicados después del 14 de octubre:
    - Next.js 16.4.0 → 16.4.2: 2 avisos críticos (ejecución remota de código sin autenticación) y 1 alto. Con ellos, `next build` se negaba a compilar.
    - `certifi` 2026.7.22 → 2026.10.19: certificados intermedios de eMudhra con llaves comprometidas. httpx los usa para hablar con Meta.
- **Evidencia:**
  - **Revisión.** Tres revisores sobre `996233a..41518c3`: `revisor-seguridad`, correctitud y completitud. Encontraron 13 hallazgos, y dos verificadores independientes (reproducción e impacto) intentaron refutar cada uno. El límite de uso cortó a los verificadores de 5 hallazgos de documentación; esos los comprobé a mano contra el código. Ninguno resultó falso. Los de impacto bajo también se corrigieron porque costaban poco, salvo los breadcrumbs de Sentry, que pasan a 1B.
  - **Cada corrección trae una prueba que fallaba antes:**
    - Seed con psql: antes corría `ALTER ROLE`, `CREATE FUNCTION` y los `INSERT`; ahora solo `BEGIN` y `ROLLBACK`.
    - 19 pruebas de configuración: contraseñas con `%`, `/` o caracteres de ancho completo, y URLs mal formadas. Ninguna cita la contraseña.
    - `within` cuando cancelan a quien llama: antes asyncio registraba "Task exception was never retrieved".
    - Webhook con la base muda: con `asyncio.timeout` tarda 11.0 s y falla; con `within`, 1 s.
  - `pnpm db:reset` y `pnpm db:test`: 6 archivos, **309 pruebas pgTAP, PASS**.
  - Backend: `uv run pytest` da **111 passed**. `ruff check`, `ruff format --check` y `mypy app tests` limpios.
  - Panel: `pnpm panel:check` en verde.
  - Dependencias: `pnpm audit --prod` y `pip-audit` sin vulnerabilidades conocidas. gitleaks, sin hallazgos en el historial.
  - `revisor-seguridad` sobre este diff: en curso al subir estos commits.
  - CI: en curso al subir estos commits.
- **Decisiones:** [003 · Revisión de cierre](decisiones/003-revision-de-cierre.md).
- **Pendiente:**
  - **La puerta (lo haces tú):** ensayo con el número de prueba y luego el número propio de Axon, con `pnpm db:test` en verde. Todo está en `docs/puerta-fase-0.md`.
  - **1F, antes de enlazar staging:**
    - Las migraciones a la nube se aplican desde CI, con aprobación.
    - Nunca uses el modo bypass en una computadora enlazada a la nube.
    - El hook 5 de `docs/hooks-propuestos.md` queda propuesto.
  - **1B:** revisar los breadcrumbs de Sentry al descargar medios.
  - `braces` (solo desarrollo, vía el lint del panel) sigue sin parche.

### 2026-10-14 · Fase 0 · Cimientos

- **Qué quedó:**
  - Monorepo con backend (FastAPI con uv), panel (Next.js sin pantallas) y Supabase.
  - Migraciones de las 16 tablas de la Fase 1, con RLS, llaves compuestas por consultorio y exclusión contra citas encimadas.
  - El rol `axon_app` fija el consultorio en cada transacción.
  - Webhook de WhatsApp firmado, deduplicado y enrutado por consultorio.
  - `/health`, comando `send-test-message`, CI en GitHub Actions y la guía de la puerta (`docs/puerta-fase-0.md`).
- **Evidencia:**
  - `pnpm db:reset` aplica 6 migraciones y el seed; `pnpm db:test` da 6 archivos, **309 pruebas pgTAP, PASS**. Al apagar a propósito la RLS de una tabla, la política de otra y los permisos del panel, las pruebas fallan como deben.
  - Backend: `uv run pytest` da **88 passed** (unitarias e integración contra la base local, incluido un proxy que congela la red). `ruff check`, `ruff format --check` y `mypy app tests` limpios.
  - Panel: `pnpm panel:check` (lint, typecheck y build) en verde.
  - `gitleaks git .`: sin hallazgos en todo el historial. `pip-audit` y `pnpm audit --prod`: sin vulnerabilidades conocidas.
  - CI en GitHub en verde en sus tres jobs (base y backend, panel y secretos): [run 1](https://github.com/JaredPS03/DENT/actions/runs/38513426600) y, con todas las correcciones, [run 3](https://github.com/JaredPS03/DENT/actions/runs/38608098324).
  - Prueba real con `fastapi run`:
    - `/health` 200 y verificación de Meta 200.
    - Aviso firmado guardado en 7 ms; sin firma, 401.
    - Ni el `verify_token` ni las IP aparecen en los logs.
  - **`revisor-seguridad`:** 4 hallazgos (1 alto, 3 medios), todos corregidos con su prueba. Cada prueba falla sin la corrección.
  - **Verificación multi-lente** (seguridad en dos pasadas, correctitud y completitud; cada hallazgo lo intentó refutar un verificador aparte): 18 hallazgos confirmados y corregidos.
    - De los 5 que no alcanzó a verificar por límite de uso, revisé 4 a mano y también se corrigieron: orden de llegada, `/health` con la red muda, fechas del seed y una prueba imprecisa.
    - El quinto (tope de 1 MB frente al tamaño máximo de Meta) queda pendiente para 1B.
    - 3 hallazgos fueron refutados.
- **Decisiones:** [001 · Cimientos](decisiones/001-cimientos.md) y [002 · Endurecimiento tras la revisión](decisiones/002-endurecimiento-tras-revision.md).
- **Pendiente:**
  - **Lo haces tú, para pasar la puerta y preparar la Fase 1:**
    - Meta Business y su verificación.
    - App con WhatsApp: número de prueba, tu celular como destinatario, token y clave secreta.
    - Seguir `docs/puerta-fase-0.md`.
    - Registro como proveedor de tecnología y coexistencia.
    - El número propio de Axon.
    - Las 5 plantillas de la Fase 1.
    - Aviso de privacidad con un abogado.
    - Búsqueda de "Axon" en el IMPI.
    - Las preguntas a tu prima de la sección "Esta semana" de la especificación.
  - **Supabase en la nube (cuando toque staging):**
    - Crear el proyecto en `us-east-1`.
    - `pnpm supabase link` y `pnpm supabase db push`, sin `--include-seed`.
    - `alter role axon_app with password '…'`.
    - `DATABASE_URL` del pooler en modo sesión con el usuario `axon_app.<project-ref>` y `?sslmode=require`.
    - Desactivar el registro abierto en Auth.
  - **GitHub y hosting:**
    - Los secretos (los de `backend/.env.example`) en el hosting, nunca en el repo.
    - Proteger la rama principal para que exija el CI.
  - **Técnico, con plazo** (detalle en la decisión 002):
    - Límite de peticiones en el webhook antes del primer despliegue público (1F).
    - Confirmar en la documentación de Meta el eco de coexistencia y el tamaño máximo de un aviso (1B).
    - Enrutar avisos de nivel de cuenta (1D).
    - Revisar el aviso de `braces` cuando salga su parche.
  - **Nota del entorno de Claude Code en la nube:** Docker Hub limitó las descargas, así que aquí solo corrió la base, no Studio ni Auth. En tu computadora `pnpm db:start` levanta todo. La documentación de Meta y `graph.facebook.com` no son alcanzables desde ese entorno.
