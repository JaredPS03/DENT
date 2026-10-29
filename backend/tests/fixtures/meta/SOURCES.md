# Payloads de ejemplo de Meta

Copiados tal cual de los ejemplos oficiales de la documentación de WhatsApp Cloud API (consultada el 2026-10-29). Las pruebas solo cambian el `phone_number_id`, el número del paciente, el id del mensaje y la hora, con `tests/support/meta_fixtures.py`, para enrutarlos a los consultorios del seed. Nunca se inventan campos.

| Archivo | Ejemplo | Fuente |
|---|---|---|
| `text.json` | Mensaje de texto | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/text |
| `text_bsuid_only.json` | Texto de un usuario con nombre de usuario, sin teléfono (solo BSUID) | https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids |
| `audio.json` | Nota de voz (`audio/ogg; codecs=opus`, `voice: true`) | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/audio |
| `template_button.json` | Botón de respuesta rápida de una plantilla | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/button |
| `list_reply.json` | Respuesta de lista | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/interactive |
| `button_reply.json` | Respuesta de botón interactivo | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/interactive |
| `unsupported.json` | Mensaje de un tipo no soportado | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/unsupported |
| `status_sent_pricing.json` | Estado `sent` con bloque `pricing` | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status |
| `status_failed.json` | Estado `failed` con el error 131049 | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status |
| `echo_text.json` | Coexistencia: texto que el negocio mandó desde la app | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_message_echoes |
| `echo_revoke.json` | Coexistencia: mensaje borrado desde la app | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_message_echoes |
| `echo_edit.json` | Coexistencia: mensaje editado desde la app | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_message_echoes |
| `app_state_sync.json` | Coexistencia: sincronización de contactos de la app | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_app_state_sync |
