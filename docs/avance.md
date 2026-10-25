# Avance

Bitácora del proyecto. Claude la actualiza al cerrar cada hito y la lee al empezar el siguiente; tú la lees para saber dónde va todo. Lo más reciente va arriba.

## Estado actual

- **Fase y hito:** Fase 1 · hito 1A cerrado (agenda y disponibilidad). La Fase 0 corre en paralelo, como dice la especificación: su código está terminado y falta su puerta con el número propio de Axon.
- **Siguiente paso:**
  - Claude empieza el 1B (WhatsApp de ida y vuelta) con el prompt de `prompts/fases.md`: primero el plan, para que Jared lo apruebe.
  - Jared pasa la puerta de la Fase 0 con `docs/puerta-fase-0.md`.
- **Bloqueos:** ninguno para el 1A. Del lado de Meta, la puerta necesita el número propio de Axon dado de alta en la API; solo Jared puede tramitarlo.

## Plantilla de cada entrada

```markdown
### AAAA-MM-DD · Hito 1A · Agenda y disponibilidad
- **Qué quedó:** lo que ya funciona, en una o dos líneas.
- **Evidencia:** comandos corridos y su resultado (pruebas, evaluaciones, revisores).
- **Decisiones:** enlaces a docs/decisiones/.
- **Pendiente:** lo que falta o quedó fuera, y por qué.
```

## Entradas

### 2026-10-25 · Hito 1A · Agenda y disponibilidad

- **Qué quedó:**
  - **Cálculo de horarios libres**, puro y en la zona del consultorio. Usa:
    - el horario semanal y el descanso;
    - el margen entre citas;
    - vacaciones y bloqueos;
    - la duración del servicio.

    Ofrece horarios cada 15 minutos y nunca en el pasado; el bot, además, solo a partir de 1 hora. Cada horario sale con un id corto y una etiqueta lista para botón ("Jue 4:00 pm").
  - **`AgendaConnector`** (buscar, crear, mover y cancelar) y su versión en Postgres, `AxonAgenda`, con eventos y `audit_log` en cada cambio.
  - **`POST /api/citas` y `PATCH /api/citas/{id}`**:
    - sesión de Supabase verificada con sus llaves públicas;
    - consultorio y rol tomados de la membresía;
    - 409 "Ese horario ya no está disponible." con alternativas;
    - límite de peticiones.
- **Evidencia:**
  - 15 pruebas de disponibilidad: los cinco casos borde del prompt y una propiedad con 300 casos de hypothesis en tres zonas horarias.
  - **Concurrencia:** 20 reservas simultáneas del mismo horario dejan exactamente 1 (19 reciben "ya no está disponible"). Pasó 6 de 6 veces seguidas.
  - **API:** 18 pruebas.
    - Sin sesión, con token vencido, de otra audiencia, de otro emisor, de otro rol, con llave desconocida o HS256 con el secreto local: 401.
    - Usuario sin consultorio: 403.
    - `clinic_id` en el cuerpo: 422.
    - Pacientes y citas de otro consultorio: 404.
    - Más peticiones que el límite: 429.
  - `uv run pytest`: **168 passed**. ruff, formato y mypy limpios. `pnpm db:test`: **316 pruebas pgTAP, PASS**, 7 nuevas para `private.clinic_memberships`. `pip-audit`: sin vulnerabilidades.
  - **`revisor-seguridad`:** 4 hallazgos de severidad media, todos reproducidos y corregidos, cada uno con su prueba:
    - un reintento podía darle al paciente dos citas a la misma hora con dos dentistas;
    - `/api` leía cuerpos de cualquier tamaño antes de verificar la sesión;
    - el limitador se volvía lento con miles de direcciones;
    - detrás del proxy, el límite por dirección habría frenado a todos los consultorios.

    Ninguno era crítico ni alto. El revisor confirmó los 4 cerrados y encontró 2 problemas nuevos de severidad media, también corregidos con su prueba:
    - el tope de cuerpo se saltaba con `--root-path`; ahora cubre todas las rutas salvo el webhook;
    - las sesiones válidas que terminaban en 403 no gastaban cupo; ahora lo gastan antes de consultar la base.

    Se corrigió además una observación menor: las IPv4 por un socket de doble pila compartían llave. Al final, `uv run pytest` da **185 passed** y pgTAP sigue en verde.
  - **Confirmación:** el revisor dio por cerradas todas las correcciones, sin problemas nuevos. CI en verde en sus tres jobs: [run 8](https://github.com/JaredPS03/DENT/actions/runs/40156396160), [run 9](https://github.com/JaredPS03/DENT/actions/runs/40157345547) y [run 10](https://github.com/JaredPS03/DENT/actions/runs/40157729282).
- **Decisiones:**
  - [004 · Alta de consultorios en la Fase 1](decisiones/004-alta-de-consultorios-en-fase-1.md);
  - [005 · Sesiones del panel y reglas de la agenda](decisiones/005-sesiones-del-panel-y-agenda.md).
- **Dependencias nuevas, aprobadas por Jared:** `PyJWT[crypto]`, `tzdata` y `hypothesis` (solo pruebas).
- **Pendiente:**
  - **1E:** que la base local firme las sesiones con llaves asimétricas, para que el panel local pueda llamar a `/api`.
  - **1E:** decidir si el panel necesita un endpoint para ver horarios libres. La especificación no lo lista; hoy el panel elige un horario y `/api/citas` lo valida.
  - **1F, condición para desplegar:** uvicorn con `--forwarded-allow-ips` limitado al proxy del hosting. Si hay más de una instancia, un limitador compartido.
  - **1E:** CORS solo para el dominio del panel.
  - **1B:** tope de largo para el motivo de cancelación cuando lo mande el bot.
  - **Antes del 1F:** el hito nuevo de alta de consultorios desde un archivo (decisión 004).

### 2026-10-25 · Fase 0 · Revisión de cierre

- **Qué quedó:**
  - El seed no aplica nada en la nube, con ningún cliente: la guarda, la contraseña y todas las filas son una sola sentencia, dentro de una transacción. Eso incluye psql con `ON_ERROR_ROLLBACK`.
  - `DATABASE_URL` se lee con el parser de libpq. Si está mal formada o lleva símbolos sin codificar, el backend no arranca y lo dice sin citarla. El error de arranque solo muestra textos propios.
  - El `wamid` (id de Meta) lleva el teléfono codificado: el comando de prueba imprime solo su final, y la evidencia de la puerta usa solo ese final.
  - asyncio ya no escribe en los logs el `repr` de tareas y excepciones. `within` limpia el trabajo abandonado en sus dos salidas.
  - `.gitignore` ignora las copias de `.env`, y `.claude/settings.json` usa comodines contra los comandos que tocan la base de la nube.
  - La guía de la puerta pide el número propio de Axon y `pnpm db:test`. El número de prueba de Meta queda como ensayo. La prueba se hace en una app de Meta solo para desarrollo, sin el número de tu prima ni de ningún consultorio.
  - Parches de seguridad publicados después del 14 de octubre:
    - Next.js 16.4.0 → 16.4.2: 2 avisos críticos (ejecución remota de código sin autenticación) y 1 alto. Con ellos, `next build` se negaba a compilar.
    - `certifi` 2026.7.22 → 2026.10.19: certificados intermedios de eMudhra con llaves comprometidas. httpx los usa para hablar con Meta.
- **Evidencia:**
  - **Revisión.** Tres revisores sobre `996233a..41518c3`: `revisor-seguridad`, correctitud y completitud. Encontraron 13 hallazgos, y dos verificadores independientes (reproducción e impacto) intentaron refutar cada uno. El límite de uso cortó a los verificadores de 5 hallazgos de documentación; esos los comprobé a mano contra el código. Ninguno resultó falso. Los de impacto bajo también se corrigieron porque costaban poco, salvo los breadcrumbs de Sentry, que pasan a 1B.
  - **`revisor-seguridad` sobre esas correcciones** (`41518c3..c7aea61`) encontró 4 hallazgos de severidad media. Los reproduje, se corrigieron, y el mismo revisor confirmó que quedaron cerrados (`c7aea61..c1ab349`), sin problemas nuevos:
    - el seed con `ON_ERROR_ROLLBACK`;
    - una `@` sin codificar en la contraseña;
    - el `wamid`, que lleva el teléfono;
    - la URL del webhook, que es de toda la app de Meta.

    Sus sugerencias menores sobre `settings.json` quedan como decisión tuya para 1F.
  - **Cada corrección trae una prueba que fallaba antes:**
    - Seed con psql, con y sin `ON_ERROR_ROLLBACK`:
      - la versión del 14 de octubre dejaba correr `ALTER ROLE` y los `INSERT`;
      - la intermedia (`c7aea61`), con `ON_ERROR_ROLLBACK`, todavía corría los `INSERT`: 21 errores en local, solo porque las filas ya existían;
      - ahora el único error es el de la guarda.
    - 25 pruebas de configuración: contraseñas con `%`, `/`, `@` o caracteres de ancho completo, y URLs mal formadas. Ninguna cita la contraseña.
    - El comando de prueba ya no imprime el `wamid` completo.
    - `within` cuando cancelan a quien llama: antes asyncio registraba "Task exception was never retrieved".
    - Webhook con la base muda: con `asyncio.timeout` tarda 11.0 s y falla; con `within`, 1 s.
  - `pnpm db:reset` y `pnpm db:test`: 6 archivos, **309 pruebas pgTAP, PASS**.
  - Backend: `uv run pytest` da **120 passed**. `ruff check`, `ruff format --check` y `mypy app tests` limpios.
  - Panel: `pnpm panel:check` en verde.
  - Dependencias: `pnpm audit --prod` y `pip-audit` sin vulnerabilidades conocidas. gitleaks, sin hallazgos en el historial.
  - CI en verde en sus tres jobs: [run 5](https://github.com/JaredPS03/DENT/actions/runs/40153508578) con la primera tanda de correcciones (`c7aea61`) y [run 6](https://github.com/JaredPS03/DENT/actions/runs/40154246992) con las de `revisor-seguridad` (`c1ab349`). La prueba del seed corre también en GitHub.
- **Decisiones:** [003 · Revisión de cierre](decisiones/003-revision-de-cierre.md).
- **Pendiente:**
  - **La puerta (lo haces tú):** ensayo con el número de prueba y luego el número propio de Axon, con `pnpm db:test` en verde. Todo está en `docs/puerta-fase-0.md`.
  - **1F, antes de enlazar staging:**
    - Las migraciones a la nube se aplican desde CI, con aprobación.
    - Nunca uses el modo bypass en una computadora enlazada a la nube.
    - El hook 5 de `docs/hooks-propuestos.md` queda propuesto.
    - Decidir si `settings.json` pide permiso también antes de `config push`, `projects delete`, `backups restore`, `storage rm` y `db dump`.
  - **1B:**
    - Revisar los breadcrumbs de Sentry al descargar medios.
    - Nunca registrar un `wamid` completo.
  - **Cuando el número de Axon pase a producción (alertas, 1D):** quitar la app de desarrollo de su WABA, para que sus avisos dejen de llegar al túnel.
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
