# 006 · WhatsApp de ida y vuelta: cola, turnos, envíos, audios y coexistencia (hito 1B)

- **Estado:** aceptada
- **Fecha:** 2026-10-29

## Contexto

El 1B une el webhook con el worker: mensajes que llegan, turnos por conversación, envíos, notas de voz y coexistencia con la app del consultorio. La especificación fija el orden por conversación, las ráfagas, la idempotencia y la ventana de 24 h. Deja abiertos el proveedor de transcripción, cómo sube el backend los audios sin la service role, cuánto calla el bot cuando contesta una persona y qué pasa con lo que escribe la asistente si la ventana ya cerró. Jared decidió esos cuatro puntos al aprobar el plan.

En la documentación de Meta se confirmaron:
- Avisos de hasta **3 MB** (antes topábamos en 1 MB y uno grande se perdía).
- Los **BSUID**, que desde abril de 2026 pueden llegar sin teléfono.
- El campo **`smb_message_echoes`** de la coexistencia.
- **`biz_opaque_callback_data`**, que regresa en los estados.
- La URL de un medio vence en 5 minutos, y el id de un medio que llega por webhook, en 7 días.

## Decisión

**Cola y worker**
- El webhook guarda cada aviso y encola solo su id, en la misma transacción.
- El worker (`python -m app.jobs.worker`) toma hasta `WORKER_CONCURRENCY` trabajos a la vez. Cada trabajo queda oculto 120 s, más que un turno completo.
- Si un trabajo falla, se reintenta con espera exponencial (5, 10, 20… hasta 300 s, con variación aleatoria). Tras 8 intentos pasa a `whatsapp_inbound_dlq` con su tipo de error. `python -m app.cli replay-dead-letters` lo devuelve a la cola cuando la causa ya se corrigió.
- Cada trabajo es idempotente por lo que señala:
  - un aviso se procesa una vez (`processed_at`);
  - un mensaje del paciente se contesta una vez (`handled_at` y la clave `reply:<id>`);
  - un envío sale como máximo una vez.

**Turnos**
- Cada conversación tiene un lock de turno con vencimiento de 120 s: dos columnas que se toman y se sueltan en transacciones cortas. Si otro worker lo tiene, el trabajo se vuelve a encolar en 2 s; nunca se descarta. Si un worker muere, el lock vence solo.
- **Ráfagas:** se contesta cuando el paciente lleva 4 s sin escribir, o a los 15 s de su primer mensaje si no para. Los tiempos se miden con el reloj de la base, el mismo de la cola.
- Si el bot está apagado en el consultorio, en pausa o el chat lo tomó una persona, el mensaje solo llega a la bandeja.

**Envíos**
- Cada mensaje a enviar se guarda antes de llamar a Meta, con su categoría de cobro y su cuerpo ya validado contra los límites de Meta.
- Justo antes de llamar a Meta se marca `send_started_at`:
  - Si Meta nunca lo recibió (sin conexión, 5xx, límites de tasa), se desmarca y se reintenta. El límite por par de números espera al menos 6 s.
  - Si se perdió la respuesta, no se reenvía y se alerta. Su estado llega después y se concilia por `biz_opaque_callback_data`, que lleva el id del mensaje en Axon.
  - Los errores de datos (4xx) no se reintentan: quedan con el código de Meta y se alertan.
- Antes de enviar se revisa la ventana (con 2 min de margen) y la lista de números de prueba.
- Los códigos de Meta se reutilizan para lo que Axon frena él mismo: 131047 si la ventana cerró, 131030 si el número no está en la lista de prueba.
- Fuera de producción, un paciente que solo tiene BSUID no recibe nada: no hay forma de compararlo con la lista.
- Los estados nunca retroceden, aunque Meta los mande en desorden. El precio que reporta Meta (`billable`, categoría) queda como verdad.

**Pacientes**
- Se dan de alta con su primer mensaje, por teléfono, por BSUID o con los dos. Cada uno completa al otro si faltaba.
- Los números de México se guardan como +52 y 10 dígitos, aunque Meta mande el "1" viejo (`521…`).
- Los mensajes de grupos de WhatsApp se ignoran: no son el chat de un paciente.

**Audios y fotos**
- Se descargan solo desde la URL que da la Graph API, por https, de hosts de Meta, sin seguir redirecciones. Tope de 16 MB, solo audio, imagen o PDF, y deben coincidir con su sha256.
- Se guardan en el bucket privado `whatsapp-media` en `<clinic_id>/<conversation_id>/<message_id>.<ext>`.
- Los sube el backend con **llaves S3 de Supabase Storage y boto3**. Esas llaves solo abren Storage, no la base, a diferencia de la service role. No hay políticas de `storage.objects`: el panel no lista ni lee el bucket directo. En el 1E verá los archivos con URLs firmadas de vida corta (`GET /api/medios/{id}`).
- Un medio que no se puede bajar (vencido, tipo o host no permitido) se abandona. Si la falla es temporal, el turno se reintenta. En el último intento el paciente recibe respuesta aunque falte el audio.

**Transcripción**
- Usa **OpenAI `gpt-transcribe`** detrás de `TranscriptionService`, llamado con httpx y sin SDK.
- No entrena con los audios y su endpoint de transcripción no guarda nada. Se pide en español, con una pista de vocabulario dental sin datos de pacientes.
- Hay una versión falsa para pruebas. Sin `TRANSCRIPTION_API_KEY`, los audios se guardan sin transcribir.

**Coexistencia**
- Lo que el consultorio manda desde su app se guarda como mensaje humano y no abre la ventana.
- El bot calla **2 horas** en ese chat (`clinics.human_reply_pause_minutes`, de 5 a 10 080), y cada mensaje nuevo desde la app renueva la pausa. Borrar o editar desde la app también la renueva.

**Panel**
- `POST /api/conversaciones/{id}/tomar` y `/soltar` cambian quién atiende. Soltar también termina la pausa.
- `/mensajes` pone el chat en modo humano y encola el texto con la clave que manda el panel, así que un doble toque se envía una vez.
- Si la ventana cerró:
  - se manda una sola vez `retomar_conversacion` ("Hola {nombre o "de nuevo"}, te escribimos de {consultorio}…");
  - el texto queda retenido (`held`) y sale en cuanto el paciente conteste;
  - si no contesta en 24 h, queda como no enviado (131047).
  - Sin la plantilla aprobada responde 409.
- Todo queda en `audit_log`, sin el texto.

**Bot de eco temporal**
- `ECHO_BOT_ENABLED` contesta "Eco: …", una vez por ráfaga. Sirve para probar la ida y vuelta con el número de prueba.
- El backend no arranca si está encendido en producción. Lo reemplaza el bot del 1C.

## Alternativas

- **Lock con `pg_advisory_lock`:** se descartó. Necesita la misma conexión durante todo el turno, y con el pooler de Supabase en modo transacción no se garantiza.
- **Reintentar envíos dudosos (al menos una vez):** se descartó. Un mensaje doble cuesta dinero y confunde al paciente. Uno perdido queda alertado y su estado lo aclara.
- **Service role para Storage:** abre toda la base sin RLS; la regla 2 la reserva para administración.
- **Firmar las peticiones S3 a mano:** evita una dependencia, pero son unas 80 líneas de criptografía propia que mantener.
- **AssemblyAI** (más barato, nombra el español de México): por defecto entrena con los audios hasta firmar su BAA. Queda como alternativa detrás de la misma interfaz.
- **Deepgram:** se descartó. Excluir el entrenamiento va en cada petición, y quedó peor en español.
- **Solo mandar la plantilla si la ventana cerró:** la asistente tendría que volver a escribir. Jared prefirió que el texto salga solo.

## Consecuencias

- Hay una dependencia nueva aprobada: `boto3`.
- Variables nuevas:
  - `ECHO_BOT_ENABLED`;
  - `TRANSCRIPTION_API_KEY` y `TRANSCRIPTION_MODEL`;
  - `STORAGE_S3_ENDPOINT`, `STORAGE_S3_REGION`, `STORAGE_S3_ACCESS_KEY_ID` y `STORAGE_S3_SECRET_ACCESS_KEY`;
  - `WORKER_CONCURRENCY`.
- Fuera de desarrollo el worker no arranca sin Storage: un audio no puede quedarse solo en Meta, donde vence a los 7 días.
- CI levanta Storage además de la base.
- El tope del webhook sube a 3 MB; `.claude/rules/seguridad.md` ya lo dice.
- La especificación ya menciona las tres funciones `security definer` y el BSUID en el modelo de datos.
- **Antes de usar audios reales:**
  - firmar el DPA de OpenAI y pedir cero retención;
  - mencionar en el aviso de privacidad que la transcripción se procesa fuera de México, y que lo valide un abogado;
  - probar con notas reales que aceptan el formato OGG/Opus. La documentación de OpenAI se contradice; si no lo aceptan, se convierte a WebM/Opus sin recodificar.
- **Quedan para hitos siguientes:**
  - 1C: el bot con IA reemplaza el eco.
  - 1D: el medidor de topes cuenta lo cobrable; el número que cambia de teléfono (y regenera su BSUID) se atiende con los avisos de sistema; la limpieza de audios y avisos vencidos; el aviso de mensajes sin respuesta en 5 min.
  - 1E: las URLs firmadas del panel y Realtime de la bandeja.
