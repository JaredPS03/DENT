# 001 · Cimientos del repositorio (Fase 0)

- **Estado:** aceptada; los puntos marcados quedaron reemplazados por [002](002-endurecimiento-tras-revision.md)
- **Fecha:** 2026-10-14

## Contexto

La Fase 0 deja listo el repositorio para construir la Fase 1 sin rehacer nada: multi-consultorio desde el primer commit, datos de salud protegidos (LFPDPPP), webhook de Meta seguro y pruebas automáticas que intentan romper la separación entre consultorios. Jared trabaja en Windows con Docker, todavía sin proyecto de Supabase en la nube ni cuenta de Meta.

## Decisión

**Repositorio**
- Monorepo con `backend/` (uv), `panel/` (Next.js) y `supabase/`. La raíz es un workspace de pnpm con la CLI de Supabase fijada como dependencia de desarrollo (`pnpm db:*`), para que en Windows baste `pnpm install`. `.gitattributes` fija LF.

**Base de datos**
- El backend entra como `axon_app` (`LOGIN NOINHERIT NOBYPASSRLS`, no es dueño de nada, sin acceso al esquema `extensions`, con `statement_timeout` de 10 s e `idle_in_transaction_session_timeout` de 30 s). Su contraseña no vive en el repo: en local la pone el seed (que desde 002 se niega a correr fuera de local); en la nube, Jared con `alter role`.
- Cada transacción empieza con `select set_config('app.clinic_id', $1, true)`, la forma parametrizada de `SET LOCAL`. Sin consultorio fijado, RLS no muestra nada.
- **Llaves foráneas compuestas** `(clinic_id, x_id)` en todas las relaciones entre tablas de negocio: la base rechaza que una fila de A apunte a un paciente, dentista, servicio o conversación de B, aunque el código se equivoque.
- `schedules`: una fila por dentista y día ISO (1 lunes … 7 domingo) con un descanso opcional y su margen entre citas. `schedule_exceptions` con `provider_id` nulo cierra todo el consultorio.
- `events.type` con `check` de los tipos de la Fase 1; cada tipo nuevo llega con una migración. Borrar a un paciente (ARCO) deja el evento sin la cita, para no perder la cifra de dinero recuperado.
- `webhook_events`: una fila por mensaje o estado, con `dedup_key` único por consultorio (`msg:<wamid>`, `status:<wamid>:<estado>`); ~~lo demás para un número conocido se guarda completo como `other`~~ *(reemplazado por 002: solo el campo `messages` guarda contenido)*.
- Se revocan los permisos que Supabase da por defecto a `anon` y `authenticated`, también para tablas futuras (`alter default privileges`). *(Para funciones, el revoke por esquema no bastaba: ver 002.)* El panel solo lee; `audit_log` y `webhook_events` no los lee.
- Cola `whatsapp_inbound` en pgmq. La crea la Fase 0 para `/health`; el encolado y el worker llegan en 1B.

**Conexión en la nube** (confirmado en la documentación de Supabase)
- Un rol propio entra por el pooler compartido con el usuario `axon_app.<project-ref>`. Para un backend persistente en una red solo IPv4 (Railway o Render) se usa el modo sesión (puerto 5432). Los prepared statements van apagados (`prepare_threshold=None`) para poder pasar al modo transacción sin cambios.
- Consecuencia para 1B: el lock por conversación no puede ser un advisory lock de sesión (se pierde en modo transacción) ni sostener una transacción mientras se espera a Meta o a la IA.

**Webhook**
- Orden: tope de 1 MB (por `Content-Length` y leyendo el stream), firma sobre el cuerpo crudo con comparación en tiempo constante, parseo estricto y tolerante a campos nuevos, enrutado por cambio. Firma inválida o ausente: 401. Firmado pero con forma inesperada: 400, para que Meta reintente mientras se corrige.
- Nombres de campos confirmados en el sample oficial de Meta (`fbsamples/whatsapp-business-jaspers-market`) y su SDK de Node, porque la documentación de Meta no se alcanza desde el entorno de Claude Code en la nube.

**Desarrollo y alertas**
- Fuera de producción (también en staging) el código bloquea cualquier envío a un número que no esté en `WHATSAPP_ALLOWED_TEST_NUMBERS`.
- Alertas de operación para Jared (hito 1D): WhatsApp desde el número de Axon con una plantilla de utilidad, y correo de Sentry como respaldo para cuando el que falla es Meta.
- Logs JSON solo con ids; las excepciones se registran por tipo y lugar, sin su mensaje (los errores de la base citan valores). Sentry sin cuerpos, cookies, variables locales ni mensajes de excepción.
- CI: pgTAP y pytest contra Supabase local, ruff, mypy, gitleaks sobre todo el historial, `pip-audit` y `pnpm audit --prod`.

## Alternativas

- **asyncpg en vez de psycopg 3:** más rápido, pero sin tipos propios y con placeholders `$1`; psycopg da SQL parametrizado, pool y tipos estrictos sin dependencias extra.
- **Contraseña de `axon_app` en la migración:** pondría un secreto en el repo. Descartado.
- **Responder 200 a un payload firmado pero inválido:** evita reintentos, pero pierde el evento en silencio. Descartado.
- **Telegram para las alertas:** gratis e independiente de Meta, pero es otra app que revisar; Jared eligió WhatsApp con correo de respaldo.

## Consecuencias

- La separación entre consultorios queda en tres capas: RLS, llaves compuestas y la regla de fijar el consultorio en cada transacción. Las pruebas de catálogo fallan si una tabla nueva llega sin RLS, sin `clinic_id`, sin índice o sin trigger.
- `pnpm audit` revisa solo lo que se despliega: hoy `braces` (vía `eslint-config-next`, solo lint) tiene un aviso alto sin versión corregida. Revisarlo cuando salga el parche.
- Pendientes que no son de la Fase 0:
  - Enrutar los avisos de nivel de cuenta (estado de plantillas, calidad del número), en 1D.
  - Confirmar el nombre exacto del eco de coexistencia contra la documentación de Meta, en 1B.
  - Límite de peticiones en `/api`, en 1A. ~~En el webhook, la firma va primero y el límite por IP se pone en el hosting~~ *(plazo y alternativas en 002)*.
  - Las plantillas propias de Axon (`alerta_equipo`, resumen y reporte) salen del número de Axon y viven en configuración, no en `templates`.
- Windows nativo: psycopg async necesita el event loop *selector*; las pruebas lo fijan, y el worker de 1B también deberá hacerlo. *(Y el backend se corre con `fastapi dev`: ver 003.)*
