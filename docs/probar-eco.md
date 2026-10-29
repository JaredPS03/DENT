# Probar el eco con el número de prueba (hito 1B)

El bot de eco contesta "Eco: …" a lo que le escribas, una sola vez por ráfaga, y con la transcripción si mandas una nota de voz. Sirve para ver la ida y vuelta completa con WhatsApp real:

**tu celular → Meta → webhook → cola → worker → Meta → tu celular**

Es temporal: el bot con IA del hito 1C lo reemplaza. Solo corre fuera de producción y solo contesta a los números de `WHATSAPP_ALLOWED_TEST_NUMBERS`.

**Usa solo el número de prueba de Meta y tu propio celular.** Nunca el número de tu prima ni datos de pacientes reales.

## 1. Lo de la puerta de la Fase 0

Haz los pasos 1 a 5 de `docs/puerta-fase-0.md`:

1. variables del backend;
2. número de prueba registrado en el consultorio ficticio A;
3. backend;
4. túnel;
5. webhook en Meta, suscrito solo a `messages`.

Si ya pasaste la puerta, solo vuelve a levantar el túnel y actualiza su URL en Meta: cambia cada vez.

## 2. Variables nuevas en `backend/.env`

Ábrelo tú; Claude no lee ese archivo.

| Variable | Valor |
| --- | --- |
| `ECHO_BOT_ENABLED` | `true` |
| `WHATSAPP_ALLOWED_TEST_NUMBERS` | Tu celular en E.164, por ejemplo `+5295XXXXXXXX`. Si Meta te lo muestra con un "1" después del 52, da igual: Axon lo normaliza. |
| `STORAGE_S3_ENDPOINT` | Lo que `pnpm supabase status` muestra como `STORAGE_S3_URL` (corre el comando en la raíz) |
| `STORAGE_S3_ACCESS_KEY_ID` | `S3_PROTOCOL_ACCESS_KEY_ID` del mismo comando |
| `STORAGE_S3_SECRET_ACCESS_KEY` | `S3_PROTOCOL_ACCESS_KEY_SECRET` |
| `STORAGE_S3_REGION` | `S3_PROTOCOL_REGION` (en local, `local`) |
| `TRANSCRIPTION_API_KEY` | Opcional. Tu llave de OpenAI, de un proyecto con el DPA firmado. Cada nota de 30 s cuesta unos US$0.002. Sin llave, el eco dice "(nota de voz sin transcribir)". |

Las llaves de Storage locales son las de fábrica de la CLI y no sirven fuera de tu computadora. En la nube se crean en **Storage > S3 Configuration** del proyecto.

## 3. Tres terminales

En `backend/`:

```bash
uv run fastapi dev app/main.py        # terminal 1: el webhook
uv run python -m app.jobs.worker      # terminal 2: el worker
```

En la terminal 3, el túnel: `cloudflared tunnel --url http://localhost:8000`.

El worker escribe `jobs.worker_started` con `"echo_bot": true`. Si dice `"media": false`, faltan las variables de Storage y las notas de voz no se guardan.

## 4. Las pruebas

Desde tu celular, escríbele al número de prueba:

1. **Un mensaje.** Manda "hola". En unos 5 segundos te llega "Eco: hola".
2. **Una ráfaga.** Manda cinco mensajes cortos seguidos, rápido. Te llega **una sola** respuesta: "Eco (5 mensajes): … / … / …".
3. **Una nota de voz.** Graba unos segundos. Te llega "Eco: (nota de voz) <lo que dijiste>", o "(nota de voz sin transcribir)" si no pusiste llave.
4. **Lo que quedó guardado.** En Supabase Studio local (<http://127.0.0.1:54323>), **SQL Editor**:

   ```sql
   select direction, sender, type, delivery_status, billing_category,
          right(meta_message_id, 8) as meta_id, media_path is not null as audio_guardado
   from public.messages
   where clinic_id = 'a0000000-0000-4000-8000-000000000001'
   order by created_at desc
   limit 10;
   ```

   Debes ver:
   - tus mensajes como `inbound`;
   - los ecos como `outbound`, enviados por `bot`, con categoría `service`;
   - el estado de cada eco avanzando a `delivered` y `read` conforme lo abres;
   - la nota de voz con `audio_guardado = true`.

   Como evidencia, guarda solo el final del id (`meta_id`), nunca el id completo: lleva tu número.

Cuando termines, regresa `ECHO_BOT_ENABLED=false`.

## Lo que esta prueba no cubre

- **Coexistencia** (lo que el consultorio manda desde su app de WhatsApp Business). El número de prueba no es de la app, así que no genera esos avisos. Las pruebas automáticas la cubren con los ejemplos oficiales de Meta. La prueba real llega al conectar un número con coexistencia, con avisos de prueba y nunca con la app de pacientes reales.
- **Tomar el chat y escribir desde el panel.** `/api/conversaciones` ya funciona y tiene sus pruebas automáticas, pero el panel llega en el hito 1E.

## Si algo falla

| Síntoma | Qué revisar |
| --- | --- |
| Llega tu mensaje a la base pero no hay eco | El worker no está corriendo (terminal 2), o `ECHO_BOT_ENABLED` no está en `true`. |
| El worker registra `whatsapp.send_refused` con `recipient_not_allowed` | Tu número no está en `WHATSAPP_ALLOWED_TEST_NUMBERS`, o no está en formato +52… |
| El worker registra `whatsapp.send_rejected` con `error_code` 131030 | En Meta, tu celular no está en la lista de destinatarios del número de prueba. |
| `error_code` 190 | El token de acceso venció: genera otro. |
| `whatsapp.send_refused` con `window_closed` | Pasaron más de 24 h desde tu último mensaje: escribe de nuevo. |
| `whatsapp.media_rejected` | La nota de voz venció en Meta o es de un tipo no permitido. Manda otra. |
| `bot.transcription_failed` | OpenAI rechazó el audio. Revisa la llave. Si dice que el formato no se acepta, avísale a Claude: hay que convertir el OGG a WebM sin recodificar (decisión 006). |
| `jobs.retry_scheduled` repetido | Meta o Storage no responden; el worker reintenta solo. Tras 8 intentos el trabajo pasa a la cola de fallidos y se devuelve con `uv run python -m app.cli replay-dead-letters`. |
