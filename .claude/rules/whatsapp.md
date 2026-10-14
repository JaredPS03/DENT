---
paths:
  - "backend/app/whatsapp/**"
  - "backend/app/webhooks/**"
  - "backend/app/jobs/**"
---

# WhatsApp Cloud API

Confirma en la documentación oficial de Meta los nombres exactos de campos y eventos antes de programarlos; no los supongas.

## Recepción

- `GET /webhooks/whatsapp`: si `hub.mode` es `subscribe` y `hub.verify_token` coincide, devuelve `hub.challenge`; si no, 403.
- `POST /webhooks/whatsapp`: valida la firma, guarda el aviso crudo en `webhook_events`, encola solo su id y responde 200 en menos de 1 s.
- Enruta al consultorio con `private.clinic_for_phone_number(metadata.phone_number_id)`; si no existe, registra y descarta.
- Un aviso puede traer varios `entry` y `changes`, incluso de consultorios distintos: sepáralos y enruta cada uno por su cuenta.
- Maneja texto, audio, imagen, documento, respuestas de botón o lista, botones de plantilla y estados de entrega (`sent`, `delivered`, `read`, `failed`).
- Deduplica por el id de mensaje de Meta.
- Coexistencia: los mensajes que el consultorio manda desde su app también llegan por webhook. Guárdalos como enviados por humano y pausa el bot en esa conversación por un tiempo configurable.

## Envío

- `POST /{phone-number-id}/messages`, con la versión de la Graph API en configuración (`META_GRAPH_VERSION`).
- Reintentos con espera exponencial para errores 5xx y límites de tasa. Un error 4xx de datos no se reintenta: se registra y se alerta.
- Ventana de 24 h: se abre o se renueva cuando el paciente escribe (`conversations.window_expires_at`). Fuera de ella, solo plantillas.
- Límites de mensajes interactivos: hasta 3 botones de respuesta con títulos de hasta 20 caracteres, listas de hasta 10 filas y cuerpo de hasta 1024 caracteres.

## Costo y topes

- Cada envío guarda su categoría: `service` (texto libre dentro de la ventana), `utility`, `marketing` o `authentication`.
- Las tarifas viven en configuración, por país y categoría y con fecha de vigencia; nunca en el código, porque Meta las cambia.
- `message_usage` suma por consultorio y por mes. Al 80% del tope avisa al dentista y a Jared.
- Al llegar al tope se siguen contestando pacientes y mandando recordatorios (el excedente se cobra); se detienen las campañas de marketing.

## Plantillas

- El catálogo vive en la tabla `templates`, con su estado sincronizado desde los avisos de Meta.
- Marketing solo a pacientes con `marketing_opt_in = true`. El botón "No recibir más" o escribir "BAJA" lo apaga al instante, y se confirma con un mensaje.

## Calidad del número

- Escucha los avisos de calidad y de límites del número. Si baja la calidad, alerta a Jared y pausa las campañas de marketing.

## Desarrollo

- Usa el número de prueba de Meta. En desarrollo, cualquier envío a un número fuera de `WHATSAPP_ALLOWED_TEST_NUMBERS` se bloquea en código.
