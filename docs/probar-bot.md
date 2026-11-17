# Probar el bot con el número de prueba (hitos 1C y 1D)

El bot contesta como asistente virtual del consultorio de prueba A (Clínica Dental Sonrisa, datos ficticios del seed):
- pide el aviso de privacidad;
- agenda, cambia y cancela citas;
- resuelve dudas del consultorio;
- pasa las urgencias al equipo.

Así ves la ida y vuelta completa con WhatsApp real:

**tu celular → Meta → webhook → cola → worker → Claude → Meta → tu celular**

**Usa solo el número de prueba de Meta y tu propio celular.** Nunca el número de tu prima ni datos de pacientes reales.

## 1. Lo de la puerta de la Fase 0

Haz los pasos 1 a 5 de `docs/puerta-fase-0.md`:
1. variables del backend;
2. número de prueba registrado en el consultorio ficticio A;
3. backend;
4. túnel;
5. webhook en Meta, suscrito solo a `messages`.

Si ya pasaste la puerta, solo vuelve a levantar el túnel y actualiza su URL en Meta: cambia cada vez.

## 2. Variables en `backend/.env`

Ábrelo tú; Claude no lee ese archivo.

| Variable | Valor |
| --- | --- |
| `ANTHROPIC_API_KEY` | Tu llave de console.anthropic.com > API keys. Cada turno cuesta alrededor de una décima de centavo de dólar. Sin llave, el bot filtra urgencias y pide el aviso, pero lo demás queda en la bandeja. |
| `WHATSAPP_ALLOWED_TEST_NUMBERS` | Tu celular en E.164, por ejemplo `+5295XXXXXXXX`. Si Meta te lo muestra con un "1" después del 52, da igual: Axon lo normaliza. |
| `STORAGE_S3_ENDPOINT`, `STORAGE_S3_ACCESS_KEY_ID`, `STORAGE_S3_SECRET_ACCESS_KEY`, `STORAGE_S3_REGION` | Lo que muestra `pnpm supabase status` como `STORAGE_S3_URL`, `S3_PROTOCOL_ACCESS_KEY_ID`, `S3_PROTOCOL_ACCESS_KEY_SECRET` y `S3_PROTOCOL_REGION` (corre el comando en la raíz). |
| `TRANSCRIPTION_API_KEY` | Opcional, para las notas de voz: tu llave de OpenAI, de un proyecto con el DPA firmado. |
| `AXON_PHONE_NUMBER_ID` | Déjala vacía en esta prueba: las alertas al equipo necesitan la plantilla `alerta_equipo` aprobada en el número de Axon. |

## 3. Tres terminales

En `backend/`:

```bash
uv run fastapi dev app/main.py        # terminal 1: el webhook
uv run python -m app.jobs.worker      # terminal 2: el worker
```

En la terminal 3, el túnel: `cloudflared tunnel --url http://localhost:8000`.

El worker escribe `jobs.worker_started` con `"bot_model": "claude-haiku-5-5"`. Si dice `null`, falta `ANTHROPIC_API_KEY`.

## 4. Las pruebas

Desde tu celular, escríbele al número de prueba:

1. **El aviso.** Manda "hola, quiero una cita para limpieza". Te llega el aviso de privacidad con los botones Acepto y No acepto.
2. **Aceptar y retomar.** Toca Acepto. El bot contesta tu primer mensaje: te ofrece horarios de limpieza en botones.
3. **Agendar.** Toca un horario. El bot confirma la cita.
4. **Una duda.** Pregunta "¿cuánto cuesta la limpieza?" o "¿dónde están?".
5. **Cambiar o cancelar.** Escribe "quiero cambiar mi cita" o "cancela mi cita".
6. **Una nota de voz**, si pusiste la llave de OpenAI: pide una cita hablando.
7. **Una urgencia.** Escribe "me duele muchísimo la muela". Te llega el mensaje de urgencia con el 911 y el chat pasa al equipo: el bot ya no contesta. Para que vuelva a contestar, en Supabase Studio local (<http://127.0.0.1:54323>), **SQL Editor**:

   ```sql
   update public.conversations set status = 'bot'
   where clinic_id = 'a0000000-0000-4000-8000-000000000001' and status = 'human';
   ```

8. **Lo que quedó registrado**, en el mismo SQL Editor:

   ```sql
   select created_at, outcome, tools, model_calls, cache_read_tokens, cost_microusd
   from public.bot_turns
   where clinic_id = 'a0000000-0000-4000-8000-000000000001'
   order by created_at desc
   limit 10;
   ```

   Desde el segundo turno, `cache_read_tokens` debe ser de miles: la parte fija del prompt sale de la caché. `cost_microusd` es el costo estimado en millonésimas de dólar.

Como evidencia guarda solo el final de los ids, nunca tu número.

## 5. El recordatorio y sus botones (hito 1D)

Antes, en Meta, da de alta en el número de prueba la plantilla `recordatorio_cita` con el texto de la especificación, en español de México (`es_MX`) y con tres respuestas rápidas: **Confirmo**, **Cambiar** y **Cancelar**, escritas así. El worker la busca aprobada y como utilidad; el seed ya la marca así en el consultorio A.

1. **Agenda una cita** con el bot para cualquier día.
2. **Tráela a 24 h** para no esperar. En el SQL Editor corre las dos sentencias en este orden (la segunda va aparte porque mover la cita vuelve a marcar cuándo se fijó su hora):

   ```sql
   update public.appointments
   set starts_at = date_trunc('minute', now()) + interval '23 hours 50 minutes',
       ends_at = date_trunc('minute', now()) + interval '24 hours 35 minutes'
   where id = (select id from public.appointments
               where clinic_id = 'a0000000-0000-4000-8000-000000000001' and status in ('pending', 'confirmed')
               order by created_at desc limit 1);

   update public.appointments set time_set_at = now() - interval '2 days'
   where id = (select id from public.appointments
               where clinic_id = 'a0000000-0000-4000-8000-000000000001' and status in ('pending', 'confirmed')
               order by created_at desc limit 1);
   ```

3. **Espera hasta 15 minutos** con el worker corriendo: te llega el recordatorio.
4. **Toca un botón:**
   - **Confirmo:** la cita queda confirmada y el bot te lo dice.
   - **Cambiar:** te ofrece hasta tres horarios; toca uno y la cita se mueve.
   - **Cancelar:** la cita se cancela y el bot te ofrece otra fecha.
5. **Lo que quedó registrado:**

   ```sql
   select type, metadata, occurred_at from public.events
   where clinic_id = 'a0000000-0000-4000-8000-000000000001'
   order by occurred_at desc limit 5;
   ```

   Cada cambio hecho desde el recordatorio lleva `{"source": "reminder"}`.

Para recibir tú las alertas de operación, pon `OPS_ALERT_PHONE` con tu número, que también debe estar en `WHATSAPP_ALLOWED_TEST_NUMBERS`, y da de alta `alerta_axon` en el número de Axon.

## Si algo falla

| Síntoma | Qué revisar |
| --- | --- |
| Llega tu mensaje a la base pero no hay respuesta | El worker no está corriendo (terminal 2). |
| Contesta "Gracias por tu mensaje. En un momento te responde el equipo…" | El modelo falló. En el log del worker, `bot.turn_failed` dice el tipo: `bad_request`, `api_error`, `timeout`… Revisa la llave. |
| `bot.model_not_configured` | Falta `ANTHROPIC_API_KEY`. |
| `bot.privacy_notice_missing` | El consultorio no tiene aviso de privacidad (`privacy_notice_url` y `privacy_notice_version`). El seed sí lo trae; corre `pnpm db:reset`. |
| `whatsapp.send_refused` con `recipient_not_allowed` | Tu número no está en `WHATSAPP_ALLOWED_TEST_NUMBERS`, o no está en formato +52… |
| `error_code` 131030 | En Meta, tu celular no está en la lista de destinatarios del número de prueba. |
| `error_code` 190 | El token de acceso venció: genera otro. |
| No llega el recordatorio | En el log del worker, `reminders.queued` dice cuántos salieron. Si dice `without_template`, la plantilla no está aprobada como utilidad en `templates`. Si dice `too_late`, la cita ya es hoy. |
| Un botón del recordatorio no hace nada | `reminders.tap_without_reminder`: la etiqueta del botón en Meta no es exactamente Confirmo, Cambiar o Cancelar. |
