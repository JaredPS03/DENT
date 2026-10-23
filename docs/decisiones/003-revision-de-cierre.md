# 003 · Revisión de cierre de la Fase 0

- **Estado:** aceptada
- **Fecha:** 2026-10-23

## Contexto

Las correcciones de la [002](002-endurecimiento-tras-revision.md) entraron después de la última revisión, y tocaron el seed, una migración, el webhook y la configuración. Antes de cerrar la fase, tres revisores las revisaron:

- `revisor-seguridad`;
- uno de correctitud;
- uno de completitud contra el prompt de la Fase 0.

Dos verificadores independientes intentaron refutar cada hallazgo: uno reproduciéndolo y otro midiendo su impacto y su alcance. El límite de uso cortó a los verificadores de cinco hallazgos de documentación; esos se comprobaron a mano contra el código. Esta decisión registra lo que cambió y reemplaza los puntos marcados en la 002.

## Decisión

**El seed no se aplica fuera de local, con ningún cliente.** `supabase/seed.sql` es una sola transacción, y la contraseña de `axon_app` se fija dentro del bloque de la guarda, después del `if`.
- Con `psql -f`, la guarda imprimía su error y psql seguía con lo demás: dejaba la contraseña pública y los consultorios falsos, y salía con código 0. La CLI y Studio sí se detenían.
- Una prueba de integración corre el archivo real con psql dentro del contenedor de la base, como si fuera la nube, y exige que no se aplique nada.

**`DATABASE_URL` se lee con el parser de libpq, en todos los entornos.**
- Un error del parser se reemplaza por un texto fijo que pide codificar los símbolos de la contraseña. libpq cita la URL completa en sus errores y el pool registraba ese texto, contraseña incluida, en cada intento de conexión. Pasaba con un `%` sin codificar, muy común en contraseñas generadas.
- También se rechaza un puerto que no sea numérico, que es lo que deja un `/` sin codificar.
- `sslmode` se lee como lo lee libpq. Antes se usaba `urlsplit`, que con un `#` en la contraseña decía que faltaba `sslmode` y con caracteres de ancho completo citaba la contraseña.
- El error de arranque solo muestra textos escritos en `config.py`, con un tipo de error propio (`axon_config`). Cualquier otro texto, sea de Pydantic o de una librería, se reduce a su tipo.

**asyncio no escribe `repr` en los logs.**
- El `lifespan` instala un manejador de excepciones del event loop. Registra un evento fijo (`asyncio.unhandled_exception`), qué falló (tarea, future o callback) y el tipo y lugar de la excepción.
- El manejador por omisión escribía el `repr` de la tarea y de la excepción, y ese texto puede citar datos.
- `within` deja en limpieza y recupera el resultado del trabajo abandonado también cuando cancelan a quien llama, no solo cuando se vence el plazo.

**Copias de `.env` fuera del repo y permisos de Claude Code más amplios.**
- `.gitignore` ignora `.env*` y `*.env`, salvo `.env.example`. Así quedan fuera copias como `.env - copia` o `produccion.env`.
- En `.claude/settings.json`, el `deny` usa comodines (`*db reset*--linked*` y sus variantes) en lugar de formas fijas. Pide permiso antes de `db push`, `link` y `migration up` o `migration repair` contra la nube.
- Es una barrera, no una garantía. Ningún patrón cubre todas las formas de escribir un comando, como ya advierte `LEEME.md`.

**La puerta se cumple con el número propio de Axon y `pnpm db:test`.**
- La especificación pide "el número real". El número de prueba de Meta queda como ensayo.
- El número de la prima no se usa para esto: sus pacientes escribirían a la computadora de Jared.

**La prueba del webhook con la base muda calienta el pool antes de congelar la red.** Sin eso, la petición nunca tomaba una conexión y la prueba también pasaba con `asyncio.timeout`, que es justo lo que la 002 descartó.

## Alternativas

- **`\set ON_ERROR_STOP on` en el seed:** la CLI le manda el archivo al servidor como SQL y esa línea falla. `psql -1` depende de que quien lo corra se acuerde.
- **Envolver `urlsplit` en un `try`:** cierra el caso de los caracteres de ancho completo, pero no el del `%` sin codificar, que es el realista.
- **Que `JsonFormatter` guarde solo la primera línea de los logs de asyncio:** no basta, porque "Exception in callback …" trae el `repr` del callback y sus argumentos en esa misma línea.
- **Más patrones de prefijo en `settings.json`:** nunca cubren todas las variantes (`pnpm run`, banderas globales antes del subcomando, `node_modules/.bin`).
- **Limpiar los breadcrumbs de Sentry:** hoy solo llevan logs propios, que tienen ids, y logs de librerías, ya sin contraseñas con la validación de `DATABASE_URL`. Se revisa en 1B, cuando se descarguen medios con URLs firmadas.

## Consecuencias

- Una contraseña de la base con símbolos debe ir codificada en `DATABASE_URL` (`%` es `%25`). El backend no arranca si no lo está y dice por qué, sin mostrarla.
- `DATABASE_URL` acepta también la forma `host=… user=… sslmode=…`, porque libpq la acepta.
- El worker de 1B y cualquier otro proceso con event loop propio debe instalar `log_asyncio_exception` en su loop.
- El `deny` comodín también bloquea un comando que solo mencione esas palabras, como un mensaje de commit que diga "db reset --linked". Para esos casos, `git commit -F archivo`.
- Las pruebas de integración necesitan `docker` en el PATH, igual que `pnpm db:start`.
- En Windows nativo el backend se corre con `fastapi dev`. Con `fastapi run` no llega a la base: sin recarga, uvicorn elige ahí el event loop Proactor, que psycopg no soporta. En WSL2, Linux y el hosting funcionan los dos.
- **Pendientes con plazo:**
  - **1F, antes de enlazar staging:**
    - Las migraciones a la nube se aplican desde CI, con un environment protegido de GitHub.
    - En una computadora enlazada a la nube nunca se trabaja en modo bypass, y al terminar se corre `supabase logout`.
    - El hook 5 de `docs/hooks-propuestos.md` queda como propuesta.
  - **1B:** revisar los breadcrumbs de Sentry (logs de librerías y URLs de httpx) al descargar medios.
