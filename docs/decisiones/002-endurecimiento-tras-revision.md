# 002 · Endurecimiento de la Fase 0 tras la revisión de seguridad

- **Estado:** aceptada
- **Fecha:** 2026-10-14

## Contexto

Antes de cerrar la Fase 0 se hicieron dos revisiones:

- `revisor-seguridad`, sobre todo el diff.
- Una verificación con varios revisores independientes:
  - seguridad, en dos pasadas;
  - correctitud;
  - completitud contra el prompt de la Fase 0.

  Cada hallazgo lo intentó refutar un verificador aparte.

Todos los hallazgos confirmados se reprodujeron contra el código o la base local. Esta decisión registra lo que cambió y reemplaza los puntos marcados en la [001](001-cimientos.md).

## Decisión

**Datos que no se guardan ni se filtran**
- **Solo el campo `messages` del webhook guarda contenido.** Cualquier otro cambio de un número conocido queda como fila `other` sin `payload`, ya purgada y con su `dedup_key` (un digest) para deduplicar. Antes se guardaba completo. Así no entran chats ni contactos sincronizados por la coexistencia, que no son pacientes.
- **Texto que Postgres no acepta** (`\u0000` y surrogates sueltos) se reemplaza por `�` en claves y valores antes de guardar. Antes, un solo carácter así tumbaba el aviso entero y Meta lo terminaba descartando.
- **Logs de uvicorn por el formateador JSON.** `configure_logging` se aplica al crear la app y otra vez en el `lifespan`, que corre después de la configuración del servidor con cualquier lanzador. Con `fastapi run`, uvicorn reconfiguraba sus logs después de importar la app: volvían el access log (con el `verify_token` en la URL) y los tracebacks en texto con su mensaje.

**Permisos**
- **`axon_app` solo actualiza las columnas de configuración de `clinics`.** Enrutamiento (`whatsapp_phone_number_id`, `waba_id`, número visible), `plan`, topes y `active` son de las tareas de administración de Axon. Lo hace la migración `restrict_clinic_updates`.
- **Funciones nuevas sin `EXECUTE` para PUBLIC, de forma global.** PostgreSQL no permite quitar ese permiso por esquema, así que el revoke de la 001 no hacía nada.
- **`supabase/seed.sql` se niega a correr fuera de la base local de la CLI.** La reconoce por su secreto JWT público de desarrollo, que un proyecto en la nube nunca tiene. `supabase db reset --linked` y `db push --include-seed` lo aplicarían a la nube con datos falsos y la contraseña pública de `axon_app`.
- **`.claude/settings.json` cubre también las formas con `pnpm`** (`pnpm db:reset --linked`, `pnpm supabase db push`, etc.). Las reglas del kit solo cubrían `supabase …`.

**Configuración**
- **Secretos de Meta con 8 caracteres o más.** Fuera de desarrollo, `DATABASE_URL` debe llevar `sslmode=require` (o `verify-ca`/`verify-full`). El error de arranque explica qué falta sin mostrar valores ni longitudes.
- **`send-test-message` no corre en producción.** Ahí todo mensaje sale por el bot, que lo registra y lo cuenta contra el tope.

**Tiempos**
- **`/health` y el webhook usan un plazo que no espera la limpieza de psycopg** (`app/core/deadlines.py`). Con la red muda, `/health` respondía en 13 s (su tope es 3) y el webhook nunca; ahora responden 503 a tiempo. Meta reintenta y la deduplicación evita duplicados.
- **El pool usa keepalives de TCP y `tcp_user_timeout`** para notar un enlace muerto en segundos.

**Orden e integridad**
- **`webhook_events.arrival_seq`**, una identidad que sigue el orden de inserción. `received_at` empata dentro de un aviso y el `id` es aleatorio; el worker de 1B lo necesita para procesar cada conversación en orden (regla 12).
- **El seed usa días hábiles en hora de Ciudad de México**, para que ninguna cita caiga en domingo o fuera de horario según el día en que se corra.

**CI**
- **Acciones fijadas por SHA de commit**, con la versión en un comentario; el checkout sin credenciales persistidas; `pip-audit` con versión fija.

## Alternativas

- **Mover la contraseña local a `supabase/roles.sql`:** corre antes de las migraciones, con otro dueño del rol, y rompe el `alter role` de la migración. Descartado: la guarda al inicio del seed protege también los datos falsos.
- **`asyncio.timeout` en lugar del plazo propio:** espera la limpieza de la conexión rota, que es justo lo que tardaba 13 s.
- **Dejar `fastapi run` y documentar `uvicorn --factory`:** depende de que nadie use el comando de siempre. Reconfigurar en el `lifespan` funciona con cualquier lanzador.

## Consecuencias

- Si una versión futura de la CLI de Supabase deja de fijar `app.settings.jwt_secret` en la base local, el seed falla con un mensaje claro y hay que cambiar la guarda.
- Una extensión nueva creada por `postgres` tampoco da `EXECUTE` a PUBLIC: hay que dar permisos explícitos a quien la use, igual que con las funciones propias.
- **Pendientes con plazo:**
  - **Límite de peticiones en el webhook, antes del primer despliegue público (hito 1F, staging).** Railway y Render no traen un límite por IP configurable: hace falta un proxy delante (Cloudflare) o un limitador en la app. Agregar ese limitador es una dependencia nueva y la decides tú.
  - **Coexistencia, al configurar el webhook en Meta:** suscribir solo el campo `messages` hasta confirmar los nombres de eco e historial en 1B (`docs/puerta-fase-0.md`).
  - **Tamaño máximo de un aviso de Meta, en 1B:** la regla del kit pide rechazar más de 1 MB. Si Meta documenta avisos más grandes, un aviso así nunca se guardaría; hay que revisarlo en la documentación oficial, que no se alcanza desde este entorno.
