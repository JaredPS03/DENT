# Puerta de la Fase 0: mandar y recibir un mensaje con el número real

La Fase 0 termina cuando mandas un mensaje por la API oficial de WhatsApp y recibes la respuesta por el webhook, guardada en la base. Esta guía lo hace en tu computadora (Windows, PowerShell o WSL2), con la base local y el número de prueba de Meta. Tardas unos 30 minutos la primera vez.

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

```powershell
pnpm db:start               # en la raíz del repo
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

Si falta o está mal una variable, no arranca y te dice cuál.

## 4. Una URL pública para tu computadora

Meta necesita llegar a tu webhook por https. Con Cloudflare Tunnel no hace falta cuenta:

```powershell
winget install --id Cloudflare.cloudflared    # una sola vez
cloudflared tunnel --url http://localhost:8000
```

Copia la dirección `https://…trycloudflare.com` que imprime. Cambia cada vez que lo corres.

## 5. Configura el webhook en Meta

En **WhatsApp > Configuración > Webhook**:

- **URL de devolución de llamada:** `https://…trycloudflare.com/webhooks/whatsapp`
- **Token de verificación:** el mismo `WHATSAPP_VERIFY_TOKEN`

Pulsa **Verificar y guardar**; Meta llama al `GET` y el backend responde con el `challenge`. Después, en los campos del webhook, suscribe **solo `messages`**. No actives `history`, `smb_app_state_sync` ni otros hasta el hito 1B: traen chats y contactos del teléfono que no son pacientes.

## 6. Manda el mensaje

```powershell
cd backend
uv run python -m app.cli send-test-message --to +5295XXXXXXXX
```

Manda la plantilla `hello_world` de Meta (en inglés). Debe imprimir `Mensaje enviado a ***XXXX. Id de Meta: wamid…`.

## 7. Recibe la respuesta

Contesta "hola" desde tu celular. En la terminal del backend aparece una línea `whatsapp_webhook.received` con `"stored": 1`. Compruébalo en el SQL Editor de Studio:

```sql
select kind, meta_id, received_at
from public.webhook_events
order by arrival_seq desc
limit 5;
```

Debe aparecer:

- una fila `message`, que es tu respuesta;
- filas `status` (`sent`, `delivered`, `read`) del mensaje que mandaste.

Con eso la puerta está cumplida. Anótala en `docs/avance.md` con la fecha y solo el `wamid` como evidencia, sin teléfonos ni texto.

## Si algo falla

| Síntoma | Causa probable |
| --- | --- |
| Meta no verifica el webhook (403) | `WHATSAPP_VERIFY_TOKEN` distinto del que pusiste en Meta, o el túnel ya cambió de dirección |
| El backend registra `whatsapp_webhook.invalid_signature` (401) | `WHATSAPP_APP_SECRET` no es la clave secreta de esa app |
| El backend registra `whatsapp_webhook.unknown_phone_number_id` | No hiciste el paso 2, o pegaste otro identificador |
| `No se envió: … no está en WHATSAPP_ALLOWED_TEST_NUMBERS` | Agrega tu celular a esa variable, en E.164 |
| `Meta respondió 400 (código 131030)` | Tu celular no está verificado como destinatario del número de prueba |
| `Meta respondió 401` | El token temporal venció (dura 24 h) |
