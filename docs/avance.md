# Avance

Bitácora del proyecto. Claude la actualiza al cerrar cada hito y la lee al empezar el siguiente; tú la lees para saber dónde va todo. Lo más reciente va arriba.

## Estado actual

- **Fase y hito:** Fase 0 · Cimientos. El código está terminado y revisado. Falta la puerta: mandar y recibir un mensaje con el número real de Meta.
- **Siguiente paso:** Jared hace la lista de "Lo haces tú" de abajo y pasa la puerta con `docs/puerta-fase-0.md`. Después, `/clear` y el prompt del hito 1A de `prompts/fases.md`.
- **Bloqueos:** la puerta necesita la app de Meta con su número de prueba, que solo puede crear Jared.

## Plantilla de cada entrada

```markdown
### AAAA-MM-DD · Hito 1A · Agenda y disponibilidad
- **Qué quedó:** lo que ya funciona, en una o dos líneas.
- **Evidencia:** comandos corridos y su resultado (pruebas, evaluaciones, revisores).
- **Decisiones:** enlaces a docs/decisiones/.
- **Pendiente:** lo que falta o quedó fuera, y por qué.
```

## Entradas

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
  - CI en GitHub: [run 1](https://github.com/JaredPS03/DENT/actions/runs/38513426600) en verde (base y backend, panel y secretos).
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
