# Puerta de la Fase 0: mandar y recibir un mensaje con el número real

La Fase 0 termina cuando se cumplen las dos mitades de su puerta (`prompts/fases.md`):

1. **Mensaje real.** Mandas un mensaje por la API oficial de WhatsApp desde el **número propio de Axon** y recibes la respuesta por el webhook, guardada en la base.
2. **Aislamiento.** Las pruebas de aislamiento entre consultorios pasan en tu computadora: `pnpm db:test`.

Esta guía lo hace en tu computadora (Windows, PowerShell o WSL2) con la base local. Primero **ensayas con el número de prueba de Meta**, que es gratis y está listo en minutos. Después repites tres pasos con el número propio de Axon cuando Meta lo apruebe (paso 8): ese es el que cuenta para la puerta. La primera vez tardas unos 30 minutos.

**No uses el número de tu prima para esta prueba.** Con su número conectado, los mensajes reales de sus pacientes llegarían a tu computadora y se guardarían en tu base local, y nunca deben salir de producción.

## Antes de empezar

- Haz "Requisitos" y "Primeros pasos" del `README.md`: Docker Desktop, Node con pnpm, uv y Git.
- Abre Docker Desktop y espera a que diga que está corriendo.
- En la raíz del repo, una sola vez: `pnpm install`. Instala la CLI de Supabase en la versión fijada del proyecto.

## Lo que necesitas de Meta

En [developers.facebook.com](https://developers.facebook.com), crea una app de tipo Empresa y agrégale el producto WhatsApp. En **WhatsApp > Configuración de la API** encuentras:

- **Número de prueba e identificador del número** (`phone_number_id`): Meta te da un número de prueba gratis.
- **Destinatarios:** agrega tu celular como número verificado; con el número de prueba solo puedes escribirle a esos números, hasta 5.
- **Token de acceso:** el temporal dura 24 h y basta para la prueba. Para algo que dure, usa el token de un usuario del sistema de tu Business Manager.

Y en **Configuración de la app > Básica**, la **clave secreta de la app**.

## 1. Variables del backend

```powershell
cd backend
copy .env.example .env      # en WSL2: cp .env.example .env
```

Llena en `backend/.env`:

| Variable | Valor |
| --- | --- |
| `WHATSAPP_APP_SECRET` | La clave secreta de la app |
| `WHATSAPP_VERIFY_TOKEN` | Una cadena larga que inventas tú (8 caracteres o más); la usas otra vez en el paso 5 |
| `WHATSAPP_ACCESS_TOKEN` | El token de acceso |
| `META_GRAPH_VERSION` | La versión de la Graph API que muestra tu app, por ejemplo `v24.0` |
| `WHATSAPP_ALLOWED_TEST_NUMBERS` | Tu celular en formato E.164, por ejemplo `+5295…` |
| `WHATSAPP_TEST_PHONE_NUMBER_ID` | El identificador del número de prueba |

Deja `ENVIRONMENT=development` y el `DATABASE_URL` local que ya trae el archivo.

## 2. Base local y número de prueba

En la raíz del repo:

```powershell
pnpm db:start               # levanta Supabase local con los dos consultorios ficticios
pnpm db:test                # la mitad de aislamiento de la puerta: debe terminar en "Result: PASS"
```

El webhook enruta cada aviso por el `phone_number_id` de su consultorio. Registra el número de prueba en el consultorio ficticio A. Abre Supabase Studio local en <http://127.0.0.1:54323>, ve a **SQL Editor** y corre:

```sql
update public.clinics
set whatsapp_phone_number_id = 'PEGA_AQUÍ_EL_IDENTIFICADOR_DEL_NÚMERO'
where id = 'a0000000-0000-4000-8000-000000000001';
```

Studio entra como administrador. El backend no puede cambiar ese dato, a propósito. `pnpm db:reset` lo borra: repítelo después de cada reset.

## 3. Backend

```powershell
cd backend
uv run fastapi dev app/main.py     # http://localhost:8000
```

Si falta o está mal una variable, no arranca y te dice cuál. En Windows nativo usa `fastapi dev`, no `fastapi run` (ver "Si algo falla").

**Déjalo corriendo** y abre otra terminal en la carpeta del repo para el paso 4.

## 4. Una URL pública para tu computadora

Meta necesita llegar a tu webhook por https. Con Cloudflare Tunnel no hace falta cuenta:

```powershell
winget install --id Cloudflare.cloudflared    # una sola vez; luego cierra y abre la terminal
cloudflared tunnel --url http://localhost:8000
```

Copia la dirección `https://…trycloudflare.com` que imprime. Cambia cada vez que lo corres. **Déjalo corriendo también** y abre una tercera terminal en la carpeta del repo para el paso 6.

## 5. Configura el webhook en Meta

En **WhatsApp > Configuración > Webhook**:

- **URL de devolución de llamada:** `https://…trycloudflare.com/webhooks/whatsapp`
- **Token de verificación:** el mismo `WHATSAPP_VERIFY_TOKEN`

Pulsa **Verificar y guardar**; Meta llama al `GET` y el backend responde con el `challenge`. Después, en los campos del webhook, suscribe **solo `messages`**. No actives `history`, `smb_app_state_sync` ni otros hasta el hito 1B: traen chats y contactos del teléfono que no son pacientes.

## 6. Manda el mensaje

En la tercera terminal:

```powershell
cd backend
uv run python -m app.cli send-test-message --to +5295XXXXXXXX
```

Manda la plantilla `hello_world` de Meta (en inglés). Debe imprimir `Mensaje enviado a ***XXXX. Id de Meta: wamid…`.

## 7. Recibe la respuesta

Contesta "hola" desde tu celular. En la terminal del backend aparece una línea `whatsapp_webhook.received` con `"stored": 1`. Compruébalo en el SQL Editor de Studio:

```sql
select kind, meta_id,
       coalesce(payload->'statuses'->0->>'status', payload->'messages'->0->>'type') as detalle,
       received_at
from public.webhook_events
order by arrival_seq desc
limit 5;
```

Debe aparecer:

- una fila `message` con detalle `text`, que es tu respuesta;
- filas `status` con detalle `sent`, `delivered` y `read`, del mensaje que mandaste. Un `failed` quiere decir que Meta no lo entregó.

Con eso el ensayo salió bien: todo el camino funciona.

## 8. La puerta, con el número propio de Axon

Cuando Meta apruebe el número propio de Axon (es parte de la Fase 0 de la especificación), repite con él:

1. **Paso 2:** registra en el consultorio A el identificador de ese número, en lugar del de prueba.
2. **Paso 6:** agrega `--phone-number-id` con ese identificador:

   ```powershell
   uv run python -m app.cli send-test-message --to +5295XXXXXXXX --phone-number-id IDENTIFICADOR_DEL_NÚMERO_DE_AXON
   ```

   Si tu cuenta no tiene la plantilla `hello_world`, usa una de tus plantillas aprobadas con `--template` y `--language` (por ejemplo `--language es_MX`).
3. **Paso 7:** comprueba tu respuesta y los estados igual que en el ensayo.

Con eso, y `pnpm db:test` en verde, la puerta está cumplida. Anótala en `docs/avance.md` con la fecha y solo el `wamid` como evidencia, sin teléfonos ni texto.

## Si algo falla

| Síntoma | Causa probable |
| --- | --- |
| `supabase` no se reconoce, o pnpm avisa `node_modules missing` | Falta `pnpm install` en la raíz del repo |
| `pnpm db:start` no puede conectarse a Docker | Docker Desktop no está abierto, o todavía está arrancando |
| `cloudflared` no se reconoce | Abre una terminal nueva después de instalarlo con `winget` |
| `/health` responde 503 con `fastapi run` en Windows | Sin recarga, uvicorn usa en Windows un event loop que psycopg no soporta: usa `fastapi dev`, o WSL2 |
| Meta no verifica el webhook (403) | `WHATSAPP_VERIFY_TOKEN` distinto del que pusiste en Meta, o el túnel ya cambió de dirección |
| Meta no verifica el webhook y el backend no registra nada | El backend o el túnel no están corriendo: cada uno va en su propia terminal |
| El backend registra `whatsapp_webhook.invalid_signature` (401) | `WHATSAPP_APP_SECRET` no es la clave secreta de esa app |
| El backend registra `whatsapp_webhook.unknown_phone_number_id` | No hiciste el paso 2, o pegaste otro identificador |
| `No se envió: … no está en WHATSAPP_ALLOWED_TEST_NUMBERS` | Agrega tu celular a esa variable, en E.164 |
| `No se envió: … DATABASE_URL …` u otra variable | El backend dice qué variable falta o está mal; si es la contraseña de la base, codifica sus símbolos (`backend/.env.example`) |
| `Meta respondió 400 (código 131030)` | Tu celular no está verificado como destinatario del número de prueba |
| `Meta respondió 401` | El token temporal venció (dura 24 h) |
