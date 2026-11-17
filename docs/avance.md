# Avance

Bitácora del proyecto. Claude la actualiza al cerrar cada hito y la lee al empezar el siguiente; tú la lees para saber dónde va todo. Lo más reciente va arriba.

## Estado actual

- **Fase y hito:** Fase 1 · hito 1C terminado en código (el bot con IA), con las evaluaciones pendientes de correr con la llave de Anthropic. La Fase 0 corre en paralelo: su código está terminado y falta su puerta con el número propio de Axon.
- **Siguiente paso:**
  - Jared corre las evaluaciones con su llave (`backend/tests/evals/README.md`) y prueba el bot con el número de prueba siguiendo `docs/probar-bot.md`.
  - Jared contesta las preguntas de producto pendientes del 1C (abajo).
  - Claude empieza el 1D con el prompt de `prompts/fases.md`: primero el plan.
  - Jared pasa la puerta de la Fase 0 con `docs/puerta-fase-0.md`.
- **Bloqueos:**
  - Las evaluaciones llaman a la API real y no hay llave en esta sesión.
  - Para audios reales hace falta firmar el DPA de OpenAI y mencionar la transcripción en el aviso de privacidad (decisión 006).
  - Antes de pacientes reales: aprobar `alerta_equipo` en Meta y que un abogado revise el aviso, el texto de "No acepto" y el borrado a las 24 h.

## Plantilla de cada entrada

```markdown
### AAAA-MM-DD · Hito 1A · Agenda y disponibilidad
- **Qué quedó:** lo que ya funciona, en una o dos líneas.
- **Evidencia:** comandos corridos y su resultado (pruebas, evaluaciones, revisores).
- **Decisiones:** enlaces a docs/decisiones/.
- **Pendiente:** lo que falta o quedó fuera, y por qué.
```

## Entradas

### 2026-11-17 · Hito 1C · El bot con IA

- **Qué quedó:**
  - **El turno** (`app/bot/orchestrator.py`), en el orden de `.claude/rules/bot-ia.md`:
    - Bot apagado, en pausa o chat tomado: el mensaje queda en la bandeja; si es urgencia, solo se alerta al equipo.
    - Filtro de urgencia determinista, antes del aviso de privacidad y de la IA.
    - Puerta de consentimiento: aviso con Acepto y No acepto; sin aceptar, la IA solo da información general; lo que escribió quien nunca aceptó se borra a las 24 h.
    - La IA (`claude-haiku-5-5`) con sus herramientas, y el mensaje fijo de respaldo si algo falla.
  - **Filtro de urgencia** (`app/bot/urgency.py`): reglas de frases cercanas por cada señal, que perdonan acentos, faltas de ortografía y letras repetidas, y reconocen cuando le pasa a otra persona. La señal de respirar o tragar no se puede quitar y se revisa primero.
  - **Herramientas** (`app/bot/tools.py`): las 8 de la Fase 1 más `responder`, en modo estricto y validadas en código. Solo se agenda un horario que el paciente vio, en un mensaje que sí salió, y que eligió en un turno posterior.
  - **`responder`** (`app/bot/reply.py`): un solo mensaje, con botones o lista; los horarios llevan la etiqueta como título y el inicio exacto como id; enlaces solo del consultorio.
  - **Alertas al equipo:** plantilla `alerta_equipo` desde el número de Axon, una por persona y sin datos de salud.
  - **Prompt v1.0** con caché (`app/bot/prompts/v1.0.md`, copia exacta de `docs/prompt-del-bot.md`) y cliente de Anthropic con la URL fijada.
  - **Registro:** cada turno queda en `bot_turns` con tokens, caché, latencia, herramientas, versión del prompt, cómo terminó y su costo estimado.
  - **Evaluaciones:** 84 casos en `backend/tests/evals/`, con revisiones en código y una rúbrica que califica `claude-sonnet-5-5`.
  - **Se quitó el bot de eco.** Guía para probar: `docs/probar-bot.md`.
- **Evidencia:**
  - `uv run pytest`: **563 passed**, 193 más que en el 1B; las evaluaciones quedan fuera porque cuestan. Entre ellas:
    - 93 frases del filtro de urgencia: 65 que disparan y 28 que no;
    - urgencia antes que el aviso y que la IA; con el chat tomado solo alerta, y respirar vuelve a alertar a los 10 minutos;
    - Acepto guarda la prueba y contesta lo que el paciente había escrito; No acepto pasa al equipo con alerta; el borrado a las 24 h;
    - no se agenda un horario que la IA no mostró, que se mostró en un mensaje que falló, ni la cita de otro paciente;
    - caché del prompt, herramientas estrictas y texto del paciente que no rompe sus etiquetas;
    - respaldo ante falla del modelo, sin `responder`, con más de 6 herramientas o en el último reintento;
    - un turno reintentado después de escalar contesta una vez y no alerta dos veces.
  - ruff, formato y mypy limpios. `pnpm db:test`: **392 pruebas pgTAP, PASS**, 55 nuevas, con las tres tablas nuevas probadas entre consultorios.
  - CI en verde: [run 21](https://github.com/JaredPS03/DENT/actions/runs/44011671420) y [run 22](https://github.com/JaredPS03/DENT/actions/runs/44013603965).
  - **Evaluaciones: sin correr.** Llaman a la API real y en esta sesión no hay llave: la petición de credencial no se pudo mostrar y la API respondió 401. Sin la llave fallan de inmediato, en lugar de pasar en falso.
  - **`revisor-seguridad`, primera ronda:** 1 alto, 5 medios y 5 bajos, todos corregidos con su prueba (detalle en la decisión 007). El alto: el borrado a las 24 h podía tocar a un paciente que ya había aceptado un aviso anterior.
  - **`revisor-seguridad`, segunda ronda:** 1 medio, 3 bajos y una nota, corregidos en `1345d1e`. Su confirmación está en curso.
  - **`revisor-clinico`:** tres rondas.
    - En la primera, el filtro de urgencia se amplió, el 911 quedó solo para respirar o tragar, y el prompt dejó de tranquilizar, dar cuidados o prometer.
    - En la segunda: fecha completa de los horarios, menos falsas alarmas, urgencias sin consentimiento marcadas como tales, y casos de evaluación más estrictos.
    - La tercera ronda, sobre los textos de `1345d1e`, está en curso.
- **Decisiones:** [007 · El bot con IA](decisiones/007-bot-con-ia.md). Jared decidió:
  - **SDK y alcance:** el SDK oficial `anthropic`; los precios del plan Esencial (2,490 de instalación y 1,490 al mes) y el Completo después.
  - **Urgencias:** con el chat en pausa, solo se alerta al equipo; la señal de respirar o tragar no se puede quitar.
  - **Consentimiento:** sin "Acepto", la IA da información general y repite el aviso; el texto de "No acepto" es "El asistente ya no te escribirá".
  - **Dudas clínicas:** primero se ofrece una valoración.
- **Dependencia nueva, aprobada por Jared:** `anthropic`.
- **Pendiente:**
  - **Jared, antes de pacientes reales:**
    - correr las evaluaciones con su llave y revisar el resumen por categoría (urgencias, inyección y dudas clínicas no pueden fallar);
    - aprobar `alerta_equipo` en Meta, con un botón de enlace al panel;
    - que un abogado revise el aviso, el texto de "No acepto" y el borrado a las 24 h.
  - **¿Declarar PyYAML?** Las evaluaciones lo usan y hoy llega con `fastapi[standard]`. Agregarlo como dependencia de desarrollo necesita tu permiso.
  - **Preguntas de producto para Jared (revisor-clinico):**
    - **P5:** señales nuevas (boca que no abre, alergia a la anestesia) y falsas alarmas aceptables ("muela rota desde hace años", "no me duele mucho").
    - **P6:** primeros auxilios para un diente que se salió completo.
    - **P7:** urgencias fuera de horario; exigir al menos un teléfono de alertas antes de encender el bot.
    - **P8:** ofrecer reagendar antes de cancelar.
    - **P9:** fotos a una persona.
    - **P10:** citas para otra persona; hoy `registrar_paciente` cambia el nombre del paciente.
    - **P11:** la dirección en la confirmación.
    - **P12:** una nota de voz sin transcribir como primer mensaje recibe el aviso; hoy pasa al equipo hasta que acepta.
    - **P13:** botones "Hoy" o "Mañana" tocados al día siguiente: usar siempre el día de la semana o que venzan a medianoche.
    - **P14:** "¿al niño le duele mucho la extracción?" dispara como urgencia.
    - **P15:** si el dentista edita el mensaje de urgencia (1E), la línea del 911 queda fija.
  - **1D:** tope diario de transcripciones por consultorio, avisos de mensajes sin respuesta y limpieza de los horarios ofrecidos viejos.
  - **1E:** que el dentista ajuste los mensajes fijos y vea los escalados en el panel.

### 2026-10-29 · Hito 1B · WhatsApp de ida y vuelta

- **Qué quedó:**
  - **Webhook → cola → worker.**
    - Cada aviso se guarda y su id se encola en la misma transacción. El tope sube a 3 MB, el máximo de Meta.
    - El worker (`uv run python -m app.jobs.worker`) reintenta con espera exponencial y manda a la cola de fallidos tras 8 intentos. `app.cli replay-dead-letters` los devuelve.
  - **Pacientes y conversaciones.** El paciente se da de alta con su primer mensaje, por teléfono o por BSUID. Los números de México quedan como +52 y 10 dígitos. Cada mensaje del paciente abre o renueva la ventana de 24 h, y un chat cerrado se reabre.
  - **Turnos.** Un worker a la vez por conversación, con un lock en Postgres que vence solo, sin transacciones abiertas mientras se espera a Meta. Una ráfaga se contesta una vez: a los 4 s de silencio, o a los 15 s si el paciente no para.
  - **Envíos.**
    - Salen como máximo una vez, con su categoría de cobro, y respetan la ventana y la lista de números de prueba.
    - `biz_opaque_callback_data` concilia los estados aunque se pierda la respuesta de Meta.
    - Los estados nunca retroceden, y el precio que reporta Meta queda guardado.
  - **Audios.** Se descargan solo de hosts de Meta y con verificación de hash, y se guardan en el bucket privado por S3. La transcripción va detrás de `TranscriptionService`, con OpenAI `gpt-transcribe` y una versión falsa.
  - **Coexistencia.** Lo que el consultorio manda desde su app queda como mensaje humano y el bot calla 2 horas en ese chat.
  - **`/api/conversaciones/{id}/tomar`, `/soltar` y `/mensajes`.**
    - Todo pasa por `audit_log`.
    - Un doble toque envía una vez.
    - Con la ventana cerrada sale `retomar_conversacion` una sola vez y el texto espera a que el paciente conteste; si no contesta en 24 h, queda como no enviado.
  - **Bot de eco temporal** (`ECHO_BOT_ENABLED`, prohibido en producción). Guía para probarlo: `docs/probar-eco.md` (en el 1C la reemplazó `docs/probar-bot.md`).
- **Evidencia:**
  - `uv run pytest`: **370 passed**, 185 más que en el 1A. Incluyen las pruebas con los ejemplos oficiales de Meta (`backend/tests/fixtures/meta`):
    - un mensaje entregado dos veces se guarda y se contesta una;
    - mensajes en desorden se contestan en el orden en que se mandaron;
    - estados en desorden no retroceden;
    - una ráfaga de 5 mensajes recibe una respuesta;
    - nota de voz guardada, transcrita y contestada;
    - estado `failed` con el código 131049;
    - eco de coexistencia que pausa el bot y no abre la ventana;
    - ventana vencida (131047);
    - paciente solo con BSUID;
    - mensaje de grupo ignorado;
    - cinco workers sobre la misma conversación contestan una vez;
    - un worker que muere con el lock no deja al paciente sin respuesta, aunque otros dos turnos lo encuentren ocupado al mismo tiempo;
    - 40 conexiones que mandan un byte a la vez no dejan fuera un aviso de Meta;
    - el tope diario de transcripciones por conversación, y un audio muy largo que se guarda sin transcribir;
    - Meta caído se reintenta y envía una vez; un error de datos no se reintenta;
    - descarga a un host que no es de Meta, rechazada sin mandar el token;
    - cola de fallidos y su reenvío;
    - la API completa con el worker enviando;
    - el bucket real por S3 rechaza tipos no permitidos y llaves falsas.
  - ruff, formato y mypy limpios. `pnpm db:test`: **337 pruebas pgTAP, PASS**, 21 nuevas. `pip-audit` y `pnpm audit --prod`: sin vulnerabilidades. gitleaks: sin secretos. `pnpm panel:check`: limpio.
  - Base local arrancada desde cero con Storage, como lo hace CI: las migraciones aplican y el bucket queda privado. CI en verde con Storage: [run 12](https://github.com/JaredPS03/DENT/actions/runs/40821177098).
  - **`revisor-seguridad`:** sin fugas entre consultorios. 1 hallazgo alto y 9 medios, todos corregidos con su prueba:
    - **(alto)** un número reciclado caía en el expediente del dueño anterior; ahora es un paciente nuevo, y del bloque `contacts` solo se usa el del remitente;
    - un 5xx de Meta podía reenviar un mensaje que sí salió; ahora solo se reintenta con los códigos de "ahora no";
    - un mensaje reclamado podía perderse si fallaba la renovación del lock; ahora se renueva antes de reclamar;
    - los ecos de coexistencia daban de alta como pacientes al laboratorio o a la familia; ahora no se guarda nada de quien no es paciente;
    - `retomar_conversacion` se habría mandado aunque Meta la pasara a marketing; ahora exige utilidad;
    - una ráfaga multiplicaba los trabajos de forma cuadrática; ahora el que encuentra el chat ocupado se retira, y cada turno atiende 20 mensajes como máximo;
    - un aviso de 1000 elementos podía no guardarse nunca; ahora se guarda en lote (de 2.2 s a 0.09 s);
    - cuerpos de 3 MB sin firma podían acaparar la memoria; ahora hay un tope de 48 MB en memoria y 10 s por lectura;
    - las llaves S3 no pasan por RLS; ahora cada lectura y escritura comprueba en código que la ruta sea del consultorio;
    - con `LOG_LEVEL=DEBUG`, botocore escribía los audios en los logs; esas librerías quedan siempre en WARNING.
  - **Revisión propia después de las correcciones:** si un worker moría antes de soltar el lock, lo pendiente esperaba a que el paciente volviera a escribir. Ahora el trabajo que encuentra el chat ocupado deja un turno de respaldo para cuando el lock vence. Solo cuenta como respaldo un turno que nadie ha tomado: la primera versión aceptaba uno en curso, y una prueba demostró que dos turnos simultáneos se cedían el respaldo y ninguno lo dejaba.
  - **Confirmación de `revisor-seguridad`:**
    - Cerró 8 de las 10 correcciones y confirmó el turno de respaldo sin problemas nuevos. Dice que también tapa dos huecos que había visto: el trabajo de un worker muerto que reaparecía con el lock vigente, y el reintento tras una falla al soltarlo.
    - Encontró 2 hallazgos medios en dos de esas correcciones. Los dos quedaron corregidos, cada uno con una prueba que falla sin la corrección:
      - **El tope del webhook contaba conexiones:** 16 conexiones que mandan un byte a la vez dejaban fuera a Meta, y con eso los mensajes de los pacientes, urgencias incluidas. Ahora cuenta bytes (48 MB), y bloquearlo exige subir unos 5 MB por segundo sin parar.
      - **El costo de transcribir seguía creciendo con una avalancha repartida en varios turnos.** Ahora también hay un tope de 20 transcripciones por conversación al día, y los audios de más de 2 MB (unos 10 a 15 minutos) se guardan sin transcribir.
    - Confirmó las dos correcciones (`99cbbef`) sin problemas nuevos, y esta vez corrió él mismo las 64 pruebas del webhook y de la ida y vuelta.
    - CI en verde con las correcciones ([run 13](https://github.com/JaredPS03/DENT/actions/runs/40822296192)) y con el turno de respaldo ([run 14](https://github.com/JaredPS03/DENT/actions/runs/43153612055)).
  - **`revisor-clinico`:** 13 observaciones.
    - Se aplicaron:
      - el eco dice que es una prueba de Axon;
      - el saludo de `retomar_conversacion` usa el primer nombre, sin títulos ni mayúsculas;
      - textos claros para la asistente en el 409 y el 422;
      - más vocabulario dental en la pista de transcripción, con una prueba de nota en silencio en la guía;
      - la pausa de coexistencia baja de 7 días a 24 h como máximo (migración nueva);
      - la plantilla exige utilidad.
    - Las que son decisiones de producto quedan abajo como preguntas para Jared.
- **Decisiones:** [006 · WhatsApp de ida y vuelta](decisiones/006-whatsapp-de-ida-y-vuelta.md). Especificación al día: tres funciones `security definer` y el BSUID. Regla de seguridad: tope de 3 MB en el webhook.
- **Dependencia nueva, aprobada por Jared:** `boto3`.
- **Pendiente:**
  - **Jared, antes de audios reales:** firmar el DPA de OpenAI y pedir cero retención; mencionar en el aviso de privacidad que la transcripción se procesa fuera de México, y que lo valide un abogado; probar que OpenAI acepta las notas OGG/Opus.
  - **1C:** el bot con IA reemplaza el eco. Una nota de voz sin transcribir (por un tope, su tamaño o una falla) debe pasar a una persona, porque podría ser una urgencia. Pasa aquí, desde el 1A, el tope de largo para el motivo de cancelación cuando lo mande el bot.
  - **1D:**
    - el medidor de topes con lo que Meta cobra;
    - los avisos de sistema (número que cambia de teléfono y regenera su BSUID);
    - limpieza de audios y avisos vencidos;
    - alerta de mensajes sin respuesta en 5 min y de la cola de fallidos.
  - **1E:**
    - `GET /api/medios/{id}` con URLs firmadas, la bandeja con Realtime y las pantallas de tomar, soltar y escribir.
    - Al dejar un mensaje en espera, avisar hasta cuándo puede salir y que evite "hoy" o "mañana"; permitir borrarlo.
    - Avisar a la asistente cuando un mensaje en espera vence o cuando el paciente vuelve después.
  - **1D:** alerta por mensajes reclamados sin id de Meta (resultado incierto) que lleven más de unos minutos.
  - **1F:** con la IP real detrás del proxy (decisión 005), un tope de lecturas por dirección en el webhook. Una inundación sostenida de unos 5 MB/s todavía provoca 503.
  - **1D:** un tope diario de transcripciones por consultorio en el medidor. Hoy el tope es por conversación, así que alguien con muchos números puede sumar costo.
  - **Preguntas para Jared (revisor-clinico), antes del 1C:**
    - ¿El filtro de urgencia corre aunque el bot esté en pausa o el chat esté tomado? Hoy la especificación revisa la pausa primero.
    - ¿Marcar contactos como "no es paciente" desde el panel?
    - ¿Cambiar el texto de `retomar_conversacion`? Se propone "Hola, somos de {{1}}. Tenemos un mensaje para ti sobre lo que nos escribiste.", con el botón "Ver mensaje", y una versión de usted. Las plantillas todavía no se mandan a Meta.
    - Cuando el paciente vuelve después de 24 h, ¿se mandan igual los mensajes en espera vencidos?

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
