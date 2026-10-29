---
paths:
  - "backend/**"
  - "supabase/**"
  - "panel/**"
---

# Seguridad y privacidad

Axon guarda datos de salud de pacientes. Un error aquí cuesta la confianza de los consultorios y puede ser una infracción a la ley de datos personales, así que estas reglas aplican a todo el código.

## Autenticación y autorización

- En `/api`, verifica el JWT de Supabase (firma, expiración y audiencia) y que el usuario pertenezca al consultorio en `clinic_users`.
- El rol limita acciones: solo el dueño cambia configuración, precios, plantillas y usuarios; la asistente opera citas y conversaciones.
- Los endpoints de administración de Axon van en una ruta aparte, con autenticación aparte (solo Jared), y todo lo que hacen queda en `audit_log`.
- Nunca confíes en un id que venga del cliente sin comprobar que pertenece al consultorio de la sesión.
- CORS solo para el dominio del panel. Límite de peticiones por usuario y por IP en `/api` y en los webhooks.

## Webhooks y archivos

- Lee el cuerpo crudo, valida la firma y solo después parsea con modelos Pydantic estrictos. Rechaza cuerpos de más de 3 MB en el webhook de WhatsApp (el máximo que documenta Meta; decisión 006) y de más de 16 KB en `/api`.
- Responde rápido y procesa en la cola.
- Descarga medios de Meta solo desde la URL que devuelve la Graph API para ese media id, por https y de dominios de Meta; con límite de tamaño y una lista blanca de tipos (audio, imagen y PDF).
- Audios y fotos se ven con URLs firmadas de vida corta que entrega `GET /api/medios/{id}`; cada entrega queda en `audit_log`.

## Datos personales

- Guarda solo lo que pide la especificación. Nunca notas clínicas ni diagnósticos.
- Logs estructurados (JSON) con ids; nunca contenido de mensajes, nombres ni teléfonos completos. Sentry con `send_default_pii=False` y un filtro que elimine cuerpos de petición y variables locales.
- Nada de datos de pacientes en URLs, nombres de archivo, `localStorage` ni mensajes de commit.
- Derechos ARCO: exportar o borrar a un paciente incluye sus mensajes, audios, fotos y citas futuras. Qué se conserva y por cuánto lo define el aviso de privacidad: deja la política configurable y marca la duda para revisión legal.
- Una tarea programada borra audios y fotos más viejos que el plazo de conservación del consultorio, y el contenido de `webhook_events` a los 7 días de procesado.

## Secretos y dependencias

- Los tokens de Meta, Google, Dentalink y Mercado Pago se guardan cifrados en Supabase Vault y se leen en tiempo de ejecución.
- Versiones fijadas (`uv.lock`, `pnpm-lock.yaml`). Escaneo de secretos (gitleaks) y de dependencias vulnerables en CI.
- Nunca `eval`, `exec` ni `pickle` con datos externos, ni SQL armado con strings: siempre parámetros.

## IA

- El texto del paciente es dato no confiable: entra al prompt dentro de etiquetas propias, sin etiquetas que imiten las del prompt, y nunca cambia permisos.
- La salida de la IA también es no confiable: valida largo, botones y enlaces (solo dominios del consultorio y de Axon) antes de enviarla.
- A la IA mándale solo lo necesario: nombre de pila, citas próximas y los últimos mensajes; nunca el historial completo ni datos de otros pacientes.
- No pongas datos de pacientes en los esquemas de las herramientas (enums, descripciones, patrones): la API guarda en caché los esquemas fuera de las protecciones del contenido.

## Revisión

Antes de cerrar un cambio en estas áreas, pide al subagente `revisor-seguridad` que revise el diff.
