# Prompts por fase

Son los mensajes que le pegas a Claude Code para construir Axon Dental, un hito a la vez. `CLAUDE.md` y `.claude/rules/` ya le dan el contexto permanente; cada prompt dice qué construir ahora y cómo saber que quedó.

## Cómo usarlos

- **Un hito por sesión.** Antes de pegar el siguiente escribe `/clear`: Claude empieza limpio y lee `docs/avance.md` para saber dónde va.
- **Modo plan en hitos grandes.** Pulsa Shift+Tab hasta ver el modo plan, pega el prompt, revisa el plan y apruébalo o corrígelo antes de que escriba código.
- **Sin evidencia no está terminado.** Cada hito dice qué tienes que ver: salida de pruebas, capturas o el reporte de los revisores.
- **Si se desvía dos veces,** usa `/clear` y vuelve a pegar el prompt con lo que aprendiste.
- **Al cerrar:** revisa el diff, haz commit y confirma que `docs/avance.md` quedó al día.

## Fase 0 · Cimientos

Puerta: envías y recibes un mensaje de prueba con el número real, y las pruebas de aislamiento entre consultorios pasan.

```text
Empezamos Axon Dental con la Fase 0: los cimientos del repositorio. No programes nada de la Fase 1.

Lee CLAUDE.md y, de docs/especificacion.md: Resumen, Fases de implementación, Arquitectura, Modelo de datos, Endpoints del backend, Requisitos no funcionales y Decisiones técnicas del kit de construcción.

Antes de planear, entrevístame con AskUserQuestion (máximo 6 preguntas, solo lo que cambie el diseño): mi sistema operativo y si Docker me funciona, si ya tengo proyecto en Supabase y repositorio en GitHub, si ya tengo el número de prueba de Meta y el número de Axon, y por qué canal quiero recibir yo las alertas de operación. Luego propón un plan y espera mi aprobación.

Entregables:
1. Monorepo con la estructura de CLAUDE.md, .gitignore (todos los .env salvo .env.example) y un README corto.
2. backend/ con uv, FastAPI y configuración con pydantic-settings que falla al arrancar si falta una variable obligatoria; .env.example completo y sin valores reales (tú no puedes leer ni crear .env: yo lo copio y pongo los valores); pytest configurado para que `uv run pytest` excluya el marcador evals.
3. panel/ con Next.js, TypeScript estricto, Tailwind y ESLint, sin pantallas todavía.
4. Migraciones de todas las tablas de la Fase 1 del modelo de datos:
   - rol axon_app con LOGIN, sin bypassrls y sin ser dueño de las tablas;
   - las funciones de .claude/rules/base-de-datos.md: private.current_clinic_id(), private.my_clinic_ids(), private.clinic_for_phone_number() y private.active_clinic_ids();
   - RLS y políticas en todas las tablas; nada para anon y solo select para authenticated;
   - btree_gist y la restricción de traslapes; triggers de updated_at;
   - seed con dos consultorios completos.
5. Pruebas pgTAP: en cada tabla, el consultorio A no lee ni escribe datos del B, ni como axon_app ni como usuario del panel; sin consultorio fijado, o con cadena vacía, no se ve nada; la segunda de dos citas encimadas se rechaza.
6. Capa de datos del backend: conexión como axon_app y un helper de transacción que siempre empieza con SET LOCAL app.clinic_id. Confirma en la documentación de Supabase cómo se conecta un rol propio a través del pooler.
7. Webhook de WhatsApp: GET de verificación y POST que valida X-Hub-Signature-256 sobre el cuerpo crudo, enruta con private.clinic_for_phone_number(), guarda en webhook_events y responde 200, sin procesar todavía. Pruebas: firma válida, inválida y ausente, cuerpo de más de 1 MB, número desconocido y evento duplicado.
8. GET /health con base de datos y cola.
9. Un comando para mandar un mensaje de prueba a un número de WHATSAPP_ALLOWED_TEST_NUMBERS; lo corro yo para pasar la puerta de la fase.
10. CI en GitHub Actions: ruff, mypy, pytest, gitleaks, supabase db reset y supabase test db, y lint, typecheck y build del panel.
11. docs/decisiones/001-cimientos.md con lo que decidiste aquí, y docs/avance.md al día.
12. Una propuesta de hooks de Claude Code que valgan la pena (por ejemplo, formatear al editar), sin configurarlos.

Terminado cuando: supabase db reset, supabase test db, pytest, ruff y mypy pasan en local y me muestras la salida, y me das la lista de lo que tengo que hacer yo a mano (Meta, Supabase en la nube, secretos en GitHub y en el hosting).
```

En paralelo, lo tuyo de la Fase 0 no es código: cuenta de Meta Business y su verificación, número de prueba, número propio de Axon para alertas y reportes, aviso de privacidad con un abogado, búsqueda de Axon en el IMPI, plantillas de la Fase 1 enviadas a Meta y las preguntas a tu prima de la sección "Esta semana" de la especificación.

## Fase 1 · Núcleo con tu prima

Seis hitos, en este orden. La puerta de la fase es operar 4 semanas con tu prima sin citas dobles ni mensajes sin respuesta.

### 1A · Agenda y disponibilidad

```text
Fase 1, hito 1A: agenda y disponibilidad. Lee docs/avance.md y, de la especificación, las funciones 1, 2, 3, 10 y 12, la fila Agenda de Tecnologías y los endpoints /api/citas. Explora, propón un plan y espera mi aprobación.

Construye:
- La interfaz AgendaConnector (buscar huecos, crear, mover, cancelar) y su implementación AxonAgenda sobre Postgres. Google Calendar y Dentalink llegan en la Fase 2: no los programes, pero que la interfaz no los impida.
- El cálculo de disponibilidad con horario semanal, descansos, excepciones, duración del servicio, margen entre citas y zona del consultorio; nunca ofrece horarios pasados. Cada horario sale con un id corto y una etiqueta de máximo 20 caracteres lista para botón ("Jue 4:00 pm").
- POST /api/citas y PATCH /api/citas/{id} con las mismas reglas que usará el bot: JWT, rol, consultorio desde el token, audit_log y eventos.
- La violación de la restricción de traslapes se convierte en "ese horario ya no está disponible", con alternativas.

Pruebas primero:
- Casos borde: cita que cruza el descanso, último horario del día, día con excepción, cambio de mes, servicio más largo que el hueco.
- Propiedades con hypothesis: ningún horario devuelto se encima con una cita activa ni sale del horario del consultorio.
- Concurrencia: 20 intentos simultáneos por el mismo horario dejan exactamente una cita.

Terminado cuando: todo pasa, me muestras la salida y revisor-seguridad no encuentra problemas en el diff. Actualiza docs/avance.md.
```

### 1B · WhatsApp de ida y vuelta

```text
Fase 1, hito 1B: WhatsApp de ida y vuelta, todavía sin IA. Lee docs/avance.md, .claude/rules/whatsapp.md y, de la especificación, Arquitectura, Flujo de conversación y Plantillas de WhatsApp. Explora, propón un plan y espera mi aprobación.

Construye:
- El worker de la cola: la cola lleva solo ids, nunca contenido; cada trabajo con clave de idempotencia; tiempo de visibilidad mayor que un turno completo; reintentos con espera exponencial y cola de fallidos.
- Orden por conversación: un mensaje a la vez con un lock en Postgres que funcione con varios workers, sin dejar una transacción abierta mientras esperas a Meta, a la IA o a la transcripción. Agrupa las ráfagas del paciente de 3 a 5 s.
- patients, conversations y messages: alta del paciente por su teléfono, estados de la conversación (bot, humano, cerrada) y ventana de 24 h.
- Cliente de envío: texto, botones, listas y plantillas; revisa la ventana antes de mandar, registra la categoría de cobro, respeta WHATSAPP_ALLOWED_TEST_NUMBERS y guarda los estados de entrega.
- Audios: descarga segura desde la Graph API, Storage privado y transcripción detrás de una interfaz TranscriptionService, con una implementación real y una falsa para pruebas. Antes de elegir proveedor, muéstrame opciones con su política de datos: no debe entrenar con los audios.
- Coexistencia: lo que el consultorio manda desde su app se guarda como mensaje humano y pausa el bot el tiempo configurado.
- POST /api/conversaciones/{id}/tomar, /soltar y /mensajes, con retomar_conversacion si la ventana ya cerró.
- Un bot de eco temporal, detrás de una bandera de configuración, para probar la ida y vuelta.

Prueba con payloads de ejemplo de la documentación de Meta, no inventados: duplicados, desorden, ráfaga de 5 mensajes en 2 s, audio, estado failed, eco de coexistencia y ventana vencida.

Terminado cuando: las pruebas pasan, me muestras la salida, revisor-seguridad revisa el diff y me dices cómo probar el eco con el número de prueba. Actualiza docs/avance.md.
```

### 1C · El bot con IA

```text
Fase 1, hito 1C: el bot con IA. Lee docs/avance.md, docs/prompt-del-bot.md y, de la especificación, Funciones que la IA puede llamar, Flujo de conversación y Criterios de aceptación de la Fase 1. Explora, propón un plan y espera mi aprobación.

Construye el orquestador con el turno de .claude/rules/bot-ia.md:
- Antes que nada, el interruptor del consultorio (clinics.bot_enabled) y la pausa de la conversación.
- Filtro de urgencia determinista con las señales del consultorio; normaliza acentos, mayúsculas y faltas comunes. Pruébalo con al menos 30 frases que deben dispararlo y 15 que no ("¿la limpieza duele?", "¿me va a doler la extracción?").
- Alerta al equipo del consultorio en el panel y con la plantilla alerta_equipo desde el número de Axon, sin datos de salud: solo el enlace a la conversación.
- Puerta de consentimiento en código, con la prueba en consents, y el borrado a las 24 h del contenido de quien no aceptó. Pregúntame qué hacer si el paciente escribe sin tocar Acepto: la especificación permite dar información general sin guardar síntomas.
- Las herramientas de la Fase 1 más responder, en modo estricto y con validación en código de cada llamada. Si la IA escala con prioridad urgente, el código manda el mensaje fijo de urgencia.
- Botones de horario: el código comprueba que el id salió de buscar_disponibilidad en esta conversación y pone la etiqueta y el inicio exacto.
- Prompt versionado desde docs/prompt-del-bot.md, con caché en la parte fija y los ejemplos ajustados a los esquemas reales de las herramientas.
- Respaldo: ante error, tiempo agotado o salida inválida, el mensaje fijo de respaldo y paso a humano con resumen.
- Registro por turno: tokens, latencia, herramientas, prompt_version y costo estimado.

Evaluaciones: al menos 50 casos en backend/tests/evals con la cobertura de bot-ia.md. Córrelas y muéstrame el resultado por categoría; ningún caso de urgencia, inyección o diagnóstico puede fallar.

Terminado cuando: pruebas y evaluaciones pasan, revisor-seguridad revisa el diff y revisor-clinico revisa el prompt y los mensajes fijos. Actualiza docs/avance.md.
```

### 1D · Recordatorios y tareas programadas

```text
Fase 1, hito 1D: recordatorios y tareas programadas. Lee docs/avance.md y, de la especificación, las funciones 4 y 5, Plantillas de WhatsApp, las tareas programadas de Endpoints del backend y Requisitos no funcionales. Explora, propón un plan y espera mi aprobación.

Construye en el worker:
- Recordatorio 24 h antes, revisado cada 15 minutos e idempotente: una cita nunca recibe dos recordatorios, aunque el worker se reinicie o corran dos.
- Los botones Confirmo, Cambiar y Cancelar de recordatorio_cita: actualizan la cita, registran el evento y, en Cambiar, ofrecen horarios.
- Catálogo de plantillas sincronizado con los avisos de Meta (aprobada, rechazada, pausada, cambio de categoría).
- Tareas a hora fija calculadas con la zona de cada consultorio, no con la del servidor.
- Limpieza: audios y fotos vencidos según el plazo del consultorio, y contenido de webhook_events procesado hace más de 7 días.
- Si una urgencia lleva 10 minutos (configurable) sin que nadie del consultorio la atienda, vuelve a avisar al equipo.
- Alertas para mí, por el canal que elegimos en la Fase 0: mensaje que el bot no contestó en 5 minutos, recordatorio fallido y baja de calidad del número.

Prueba con reloj simulado: cita creada con menos de 24 h, cita cancelada antes del envío, botón tocado dos veces y worker reiniciado a mitad del envío.

Terminado cuando: las pruebas pasan, me muestras la salida y revisor-seguridad revisa el diff. Actualiza docs/avance.md.
```

### 1E · Panel

```text
Fase 1, hito 1E: el panel. Lee docs/avance.md, .claude/rules/panel.md y, de la especificación, Pantallas del panel y Endpoints del backend. Antes de programar, muéstrame un boceto simple de cada pantalla para aprobarlo.

Construye las seis pantallas de la Fase 1: Entrar, Hoy, Agenda, Conversaciones, Paciente y Configuración.
- Primero el celular (360 px), después escritorio. Instalable como PWA.
- Lecturas directas con RLS; escrituras por /api. La bandeja se actualiza en tiempo real.
- Audios con su transcripción y el resumen del bot; las URLs firmadas las da GET /api/medios/{id}, que registra cada acceso.
- Tomar el control y soltarlo, con el estado visible para que nadie conteste encima del bot.
- Paciente: exportar y borrar sus datos (ARCO), con confirmación, sus endpoints y sus pruebas.
- Configuración: horarios, servicios, precios, señales de urgencia y textos del bot; solo el dueño la cambia.
- Estados vacíos, de carga y de error en español.

Terminado cuando: lint, typecheck y build pasan; tomas capturas con Playwright a 360 px y en escritorio, las revisas tú y me las muestras; revisor-clinico revisa los textos y revisor-seguridad el diff. Actualiza docs/avance.md.
```

### 1F · Cierre de la Fase 1

```text
Fase 1, hito 1F: cierre. Lee docs/avance.md y la sección Criterios de aceptación de la Fase 1.

1. Arma una tabla con cada criterio, la prueba que lo demuestra y su evidencia (salida, captura o caso de evaluación). Lo que no esté cubierto, cúbrelo.
2. Prueba de carga con 10 consultorios simulados y ráfagas de mensajes: mide la respuesta del webhook y la del paciente contra los Requisitos no funcionales.
3. Pide a revisor-seguridad una revisión completa del código, no solo del último diff, y a revisor-clinico de todos los textos que ve un paciente.
4. Escribe docs/operacion.md: cómo desplegar, variables y secretos, cómo rotar llaves, cómo restaurar un respaldo (pruébalo en staging), qué hacer si fallan Meta, la IA o la base, y cómo apagar el bot de un consultorio.
5. Despliega a staging y dame la lista de verificación para el día del piloto con mi prima.

No despliegues a producción: lo apruebo yo después de revisar todo. Actualiza docs/avance.md.
```

## Fases 2 a 4

Primero planeas la fase con su prompt; después corres cada hito del plan con el prompt de "Un hito de una fase planeada".

### Fase 2 · Vendible

```text
Vamos a planear la Fase 2. Lee docs/avance.md y, de la especificación, la Fase 2 en Fases de implementación y todo lo marcado con Fase 2 en Funcionalidades, Modelo de datos, Plantillas, Pantallas y Endpoints.

Divide la fase en hitos de 1 a 3 días. Para cada uno: qué entrega, qué tablas, endpoints y pantallas toca, sus pruebas, su criterio de terminado y qué revisor aplica. Ordénalos por dependencias y marca riesgos y lo que depende de terceros. Escríbelo en docs/plan-fase-2.md y no programes hasta que lo apruebe.

Ten en cuenta:
- Alta integrada de Meta (Embedded Signup) y registro como proveedor de tecnología: confirma los requisitos vigentes en la documentación de Meta. Los tokens de cada consultorio van en Vault.
- Importar pacientes: la IA solo propone; nada se guarda sin que el dentista lo revise. Duplicados por teléfono. Los importados no tienen permiso de marketing.
- Google Calendar: permisos mínimos, tokens en Vault y sincronización en ambos sentidos sin duplicar citas (id externo). Dentalink solo si ya hay acceso confirmado a su API; si no, queda fuera.
- Medidor y topes: tarifas en configuración con fecha de vigencia; al tope nunca se deja de contestar ni de recordar.
- Reporte mensual: solo cuentan citas con asistencia, al precio registrado; cada cifra se rastrea hasta sus eventos.
- Mi mensualidad con Mercado Pago: firma del webhook, idempotencia por id de pago, y defíneme qué pasa cuando un pago falla (aviso, gracia, pausa).
- El resumen diario y el reporte salen del número de Axon, como las alertas; confírmalo conmigo antes de crear sus plantillas.
- Administración de Axon: ruta y autenticación aparte, y todo queda en audit_log.
```

### Fase 3 · Retención

```text
Vamos a planear la Fase 3. Lee docs/avance.md y, de la especificación, la Fase 3 en Fases de implementación, el Plus 4 y todo lo marcado con Fase 3 en Funcionalidades, Modelo de datos, Plantillas, Pantallas y Endpoints.

Divide la fase en hitos de 1 a 3 días. Para cada uno: qué entrega, qué tablas, endpoints y pantallas toca, sus pruebas, su criterio de terminado y qué revisor aplica. Ordénalos por dependencias y marca riesgos. Escríbelo en docs/plan-fase-3.md y no programes hasta que lo apruebe.

Ten en cuenta:
- Marketing solo con permiso y siempre con opción de baja, un tope de frecuencia por paciente y pausa automática si baja la calidad del número.
- Reseñas: el enlace de Google va a todos los pacientes atendidos, no solo a quienes calificaron alto, porque Google prohíbe pedir reseñas de forma selectiva. Una calificación baja, además, avisa al dentista.
- Lista de espera: ofrece cada hueco a una persona a la vez, o al primero que acepte, con la restricción de traslapes como árbitro final.
- Tablero: los mismos números del reporte mensual, con el detalle hasta cada cita; mejor corto que inflado.
- revisor-clinico revisa cada plantilla de marketing: la publicidad de servicios de salud está regulada.
```

### Fase 4 · Fotos, anticipos y tratamientos largos

```text
Vamos a planear la Fase 4. Lee docs/avance.md y, de la especificación, la Fase 4 en Fases de implementación, los Plus 2 y 3, y todo lo marcado con Fase 4 en Funcionalidades, Modelo de datos, Plantillas, Pantallas y Endpoints.

Divide la fase en hitos de 1 a 3 días. Para cada uno: qué entrega, qué tablas, endpoints y pantallas toca, sus pruebas, su criterio de terminado y qué revisor aplica. Ordénalos por dependencias y marca riesgos. Escríbelo en docs/plan-fase-4.md y no programes hasta que lo apruebe.

Ten en cuenta:
- Anticipos con Mercado Pago en la cuenta de cada consultorio: Axon nunca recibe el dinero. Firma del webhook, idempotencia y liberación del horario si no se paga en el plazo.
- Fotos: se clasifican (diente, comprobante, otro) sin diagnosticar; preguntas de triage; con señales de alerta pasa a humano.
- Comprobantes: monto, fecha y referencia quedan pendientes hasta que el consultorio los aprueba; nunca confirman una cita por sí solos.
- Tratamientos: solo planes y textos que el dentista escribió o aprobó; Axon no inventa protocolos clínicos.
- Evaluaciones nuevas para fotos, comprobantes y seguimiento antes de activarlos con pacientes.
```

### Un hito de una fase planeada

```text
Hito [número] de docs/plan-fase-[N].md. Lee docs/avance.md y la descripción del hito. Explora, propón un plan y espera mi aprobación. Pruebas primero; al terminar corre lint, tipos y pruebas, muéstrame la salida, pide los revisores que apliquen y actualiza docs/avance.md.
```

## Mantenimiento

### Revisar antes de cerrar

```text
Revisa el trabajo de esta rama antes de cerrarla. Corre lint, tipos, pruebas y, si tocaste el bot, las evaluaciones. Pide a revisor-seguridad que revise git diff main...HEAD y, si cambiaron textos que ve un paciente o un dentista, a revisor-clinico. Corrige lo que encuentren, vuelve a correr todo y dame un resumen: qué cambió, la evidencia y los riesgos pendientes.
```

### Error en producción

```text
Hay un error en producción: [qué pasó, cuándo y el id del consultorio; nunca datos de pacientes].
1. Busca la causa en el código y en los logs por ids. No pidas ni copies datos reales de pacientes.
2. Escribe primero una prueba que reproduzca el fallo y muéstrame que falla.
3. Corrige con el cambio mínimo y muéstrame que esa prueba y todas las demás pasan.
4. Si el error fue del bot, agrégalo como caso de evaluación.
5. Deja una nota corta en docs/incidentes/: qué pasó, a quién afectó, la causa, la corrección y cómo evitarlo.
```

### Cambiar el comportamiento del bot

```text
Quiero cambiar el comportamiento del bot: [qué y por qué].
1. Corre las evaluaciones con la versión actual del prompt y guarda el resultado.
2. Escribe una versión nueva en docs/prompt-del-bot.md y agrega casos de evaluación para el cambio.
3. Corre las evaluaciones con la versión nueva y compáralas por categoría con las anteriores.
4. Pide revisión a revisor-clinico.
Si alguna categoría empeora, no la actives: muéstrame la comparación.
```

### Revisión mensual de WhatsApp

```text
Revisión mensual de WhatsApp. En la documentación oficial de Meta, revisa los precios para México por categoría, la política para bots de IA, la versión vigente de la Graph API y los avisos de deprecación. Compáralos con la configuración y con docs/especificacion.md y propón los cambios (tarifas con fecha de vigencia, versión de la API). No cambies nada sin mi aprobación.
```
