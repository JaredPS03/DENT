# 008 · Recordatorios y tareas programadas (hito 1D)

- **Estado:** aceptada
- **Fecha:** 2026-11-17

## Contexto

El 1D agrega lo que corre sin que nadie escriba: el recordatorio 24 h antes de cada cita con sus botones (funciones 4 y 5), el catálogo de plantillas sincronizado con Meta, la limpieza de lo que vence, el segundo aviso de una urgencia sin atender y las alertas de operación para Jared.

Jared aprobó el plan con estas ocho decisiones:
1. **Cita agendada o movida con menos de 24 h:** no lleva recordatorio, porque el paciente acaba de recibir la confirmación.
2. **Chat tomado por el equipo o bot apagado:**
   - Confirmo y Cancelar actualizan la cita, pero no se le contesta al paciente (criterio de la Fase 1);
   - Cambiar queda para el equipo;
   - los recordatorios salen aunque el bot del consultorio esté apagado.
3. **Cancelar cancela directo,** sin preguntar "¿seguro?".
4. **"Avisar al consultorio" (función 5)** es que la cita se vea en la agenda y la bandeja del panel. No hay un WhatsApp extra al equipo por cada confirmación o cancelación.
5. **Audios y fotos se guardan 90 días** por defecto, configurable por consultorio. Va a la lista del abogado.
6. **Una sola plantilla de recordatorio** para tú y usted.
7. **El medidor de mensajes y los topes por plan se quedan en la Fase 2,** como dice la especificación (función 20).
8. **El cambio de número de un paciente** (mensaje de sistema de Meta) queda para el 1F.

## Decisión

**El programador vive en el worker** (`app/jobs/scheduler.py`):
- Cada 20 segundos mira el reloj. Por cada tarea con turno vencido, reclama el turno en la base (`private.claim`) y, en la misma transacción, encola un trabajo por consultorio activo. Otro worker o un reinicio encuentran el turno tomado.
- Las tareas son tres:
  - **recordatorios**, cada 15 minutos;
  - **vigilancia**, cada minuto: segundo aviso de urgencias, mensajes sin respuesta, envíos con resultado incierto y recordatorios fallidos;
  - **limpieza**, cada hora, que solo actúa a las 3:00 en la zona de cada consultorio.
- Un turno perdido mientras no había worker no se repite: cada tarea busca lo que está pendiente, no lo de su turno.
- Cada minuto manda una señal de vida a Sentry (Cron Monitors, en `sentry-sdk`, sin dependencia nueva). Si deja de llegar, Sentry avisa a Jared por correo. Las alertas por WhatsApp salen de este mismo worker y no podrían avisar de su propia caída.

**El recordatorio** (`app/jobs/reminders.py`):
- Busca las citas pendientes o confirmadas de las próximas 24 h que no tengan recordatorio para su hora actual y cuyo horario se fijó al menos 24 h antes. `appointments.time_set_at` lo marca un trigger cada vez que cambia `starts_at`.
- `appointment_reminders` guarda uno por cita y hora (llave única). Ni dos workers ni un reintento ponen dos en cola. Una cita movida recibe el suyo para la hora nueva.
- La plantilla entra a la cola de salida de la conversación, la misma que ya envía cada mensaje a lo más una vez. Queda a la vista en la bandeja con su texto.
- Solo sale si `recordatorio_cita` está aprobada **como utilidad**. Si Meta la pausa o la pasa a marketing, no sale, se reintenta en el siguiente turno y Jared recibe una alerta.
- La plantilla dice "mañana": si el worker estuvo caído y la cita ya es hoy, el recordatorio queda omitido (`too_late`) y Jared recibe una alerta.
- **Cita cancelada o movida antes del envío:** el recordatorio en cola se anula en la misma transacción del cambio. Justo antes de llamar a Meta se revisa otra vez, por si la cita cambió por otro camino.

**Los botones** (`app/bot/reminder_taps.py`) son código, sin IA. Corren antes que la IA y que la puerta de consentimiento, porque solo tocan la cita:
- **Ligar el toque a su recordatorio:** Meta devuelve el toque de una respuesta rápida de plantilla con el texto del botón, no con un id propio. Así lo documenta su referencia del webhook `messages/button`, y el ejemplo trae el mismo texto en `payload` y `text`. Por eso se liga por `context.id`, el wamid de la plantilla que el paciente contesta, y solo dentro de su propia conversación. Si ese id no se conoce (el envío quedó incierto), toma el último recordatorio de esa conversación de las últimas 48 h.
- **Confirmo y Cancelar:** cambian la cita y registran el evento con `{"source": "reminder"}` para el reporte mensual. Un segundo toque no cambia nada ni registra otro evento.
- **Cambiar:** ofrece hasta 3 horarios libres del mismo servicio, el primero de cada día en los próximos 7 días, como botones `mover:<recordatorio>:<horario>`. La cita no se toca hasta que el paciente elige. Al elegir, el código comprueba que ese horario se mostró en esa conversación, en un mensaje que llegó, y mueve la cita.
- **Recordatorio viejo:** si la cita ya se canceló, ya pasó o cambió de hora, el mensaje lo dice y no actúa.
- **Ráfaga con más mensajes:** si además del toque el paciente escribió algo, el toque se aplica y el resto lo contesta la IA como siempre.

**Los avisos de la cuenta** (`app/whatsapp/account.py`):
- Llegan sin `phone_number_id`, bajo `entry[].id`, la cuenta de WhatsApp Business. Se guardan para cada consultorio de esa cuenta (`private.clinics_for_waba`) con su contenido, que no trae datos de pacientes, y se procesan en la cola.
- Los campos se confirmaron en la referencia de webhooks de Meta: `message_template_status_update`, `template_category_update`, `phone_number_quality_update` y `account_update`.
- **Plantillas:** su estado y su categoría actualizan `templates`. Hay alerta si una plantilla se rechaza, se pausa, se desactiva, se marca con baja calidad o cambia de categoría.
- **Calidad del número:** el aviso solo se toma si es del número del consultorio. Un evento desconocido o una baja de límite cuentan como mala noticia, porque la página de Meta lista menos eventos de los que ha mandado.
- **La cuenta de Axon** (`AXON_WABA_ID`) no tiene consultorio: sus avisos son alerta directa para Jared.

**El segundo aviso de una urgencia:**
- Se manda si una urgencia sigue abierta pasados los minutos del consultorio (`clinics.urgent_realert_minutes`: 10 por defecto, de 5 a 60) sin respuesta del equipo, ni desde el panel ni desde la app del consultorio.
- `alerta_equipo` sale otra vez, una sola vez (`team_alerts.round` = 2).
- No se avisa otra vez de urgencias de más de 6 horas.

**Las alertas para Jared** (`app/jobs/ops_alerts.py`):
- Cada una deja una línea de error en Sentry (el respaldo por correo) y sale por WhatsApp desde el número de Axon con la plantilla nueva `alerta_axon`.
- Sale a lo más una por tipo y consultorio cada hora (`private.claim`), con el número de casos. Solo llevan el tipo, el nombre del consultorio y un conteo.
- Los tipos son:
  - mensaje sin respuesta a los 5 minutos;
  - envío con resultado incierto;
  - recordatorio que no salió;
  - plantilla;
  - calidad del número;
  - restricción de la cuenta;
  - trabajos en la cola de fallidos.

**La limpieza:**
- Audios y fotos más viejos que `clinics.media_retention_days` (90 por defecto): primero se suelta la fila y después se borra el archivo; la transcripción se queda.
- Archivos de más de un día a los que ningún mensaje apunta, los que dejó el borrado a las 24 h.
- Contenido de `webhook_events` procesado hace más de 7 días.
- Horarios ofrecidos de más de 7 días.

**El tope diario de transcripciones por consultorio** (`TRANSCRIPTION_DAILY_CAP_PER_CLINIC`, 300 por defecto) se suma al de cada conversación. Pasándolo, la nota de voz va al equipo sin transcribir, como una que no se pudo escuchar.

**Base de datos** (migración `reminders_and_scheduled_jobs`):
- `appointment_reminders`, con RLS solo para el backend;
- `appointments.time_set_at` y su trigger;
- `messages.context_meta_message_id`;
- `clinics.media_retention_days` y `urgent_realert_minutes`;
- `escalations.realerted_at` y `team_alerts.round`;
- `templates.status_reason`;
- el tipo `account` en `webhook_events`;
- `private.claims` con `private.claim()` y `private.clinics_for_waba()`;
- permiso de borrar horarios ofrecidos.

## Alternativas

- **pg_cron que encole los trabajos desde la base:** no necesita un ciclo en el worker, pero deja lógica en SQL y es más difícil de probar con reloj simulado. El turno reclamado da la misma garantía de "una vez" con varios workers.
- **Un id propio en cada botón de la plantilla** (`payload` al enviar): no se confirmó en la documentación de Meta. Si Meta lo rechazara, se caerían todos los recordatorios. `context.id` sí está documentado.
- **Que la IA atienda los botones:** cuesta tokens, depende de las evaluaciones y no funciona sin llave. Los botones ya traen la decisión del paciente.
- **Marcar el recordatorio en la cita (`reminder_sent_at`):** no distingue la hora que anunció. Una cita movida se quedaría sin recordatorio o recibiría uno viejo.
- **Telegram o solo correo para las alertas:** se mantiene la decisión 001, WhatsApp con correo de Sentry de respaldo.

## Consecuencias

- **Variables nuevas:** `AXON_WABA_ID`, `OPS_ALERT_PHONE` y `TRANSCRIPTION_DAILY_CAP_PER_CLINIC`.
- **Para Jared, antes de producción:**
  - dar de alta en Meta `recordatorio_cita` con el texto nuevo y tres respuestas rápidas con estas etiquetas exactas: Confirmo, Cambiar y Cancelar;
  - dar de alta `alerta_axon` en el número de Axon;
  - suscribir la app a `message_template_status_update`, `template_category_update`, `phone_number_quality_update` y `account_update`;
  - en Sentry, una regla de correo para `ops.alert` y para el monitor `axon-worker`.
- **Pendiente:**
  - el abogado revisa el plazo de 90 días;
  - el panel (1E) deja editar el plazo y los minutos del segundo aviso, y muestra el estado del recordatorio;
  - el cambio de número (1F) y el medidor de mensajes (Fase 2).
