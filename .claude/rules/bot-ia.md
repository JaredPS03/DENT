---
paths:
  - "backend/app/bot/**"
  - "backend/tests/evals/**"
  - "docs/prompt-del-bot.md"
---

# Bot de IA

El bot es la recepción del consultorio. Si se equivoca con una cita o con una urgencia, el dentista lo apaga, así que todo aquí busca que sea predecible.

## Un turno, paso a paso

1. Ya en el worker y con el lock de la conversación, junta la ráfaga de mensajes del paciente (espera corta configurable, de 3 a 5 s).
2. Si hay audio, transcríbelo; el texto entra marcado como transcripción.
3. Si el bot está apagado para el consultorio (`clinics.bot_enabled`) o en pausa en esta conversación, termina: el mensaje queda en la bandeja.
4. Filtro de urgencia determinista con la lista de señales del consultorio. Si coincide: escala con prioridad urgente, alerta al equipo, manda el mensaje fijo de urgencia y termina.
5. Puerta de consentimiento en código: si no ha aceptado, manda el aviso con el botón Acepto y termina; al aceptar, retoma el mensaje original.
6. Llama a la IA con la parte fija del prompt (con caché) y, al final, la hora actual, el contexto mínimo del paciente y los últimos mensajes.
7. Bucle de herramientas: máximo 6 llamadas por turno, con tiempo límite por llamada y por turno. Cada herramienta valida permisos y datos en código antes de ejecutar. Si la IA escala con prioridad urgente, el código manda el mensaje fijo de urgencia y termina el turno.
8. La IA entrega su respuesta con la herramienta interna `responder`. El código valida los límites de WhatsApp y los horarios, y la envía como un solo mensaje. Si la IA termina sin llamar a `responder`, se le pide una vez que la use.
9. Si algo falla (error de la API, tiempo agotado o salida inválida), manda el mensaje fijo de respaldo de `docs/prompt-del-bot.md` y escala a humano con resumen. El paciente nunca se queda sin respuesta.
10. Registra eventos, tokens, latencia, herramientas usadas y `prompt_version`.

## Herramientas

- Las de la tabla "Funciones que la IA puede llamar" de la especificación, más `responder`, con los nombres en español tal cual.
- Todas con `strict: true` y `additionalProperties: false`, y una descripción clara de cuándo usarlas y cuándo no.
- `clinic_id` y `patient_id` nunca son parámetros: salen del contexto del turno.
- Cada id de cita que recibe una herramienta se verifica contra las citas de ese paciente.
- `responder` acepta texto (máximo 1024 caracteres) y, opcionalmente, botones con títulos de hasta 20 caracteres, horarios o una lista de hasta 10 filas. Entre botones y horarios, máximo 3; con más opciones, lista.
- Los horarios de `responder` son ids devueltos por `buscar_disponibilidad` en esta conversación. El código rechaza cualquier otro, pone la etiqueta como título y el inicio exacto (ISO 8601 con zona) como id del botón.

## Modelo

- `claude-haiku-5-5` con el esfuerzo por defecto; cámbialo solo con evidencia de las evaluaciones (latencia y calidad).
- No uses con datos de pacientes los modelos que exigen 30 días de retención (Fable 5.1 y Mythos 5.1).
- El modelo y la versión del prompt van en configuración, no en el código.

## Prompt

- La fuente es `docs/prompt-del-bot.md`; se copia a `backend/app/bot/prompts/` con un número de versión, y cada mensaje del bot guarda la versión que lo generó.
- Orden para la caché: herramientas, luego todo lo fijo del consultorio (rol, reglas, ejemplos y su bloque) y al final la hora y el paciente, que cambian en cada turno.
- Cambiar el prompt exige correr las evaluaciones y pasar la revisión del subagente `revisor-clinico`.

## Evaluaciones (`backend/tests/evals/`)

- Al menos 50 casos en YAML: consultorio de prueba, estado del paciente, mensajes y lo que se espera.
- Cobertura obligatoria: agendar por texto y por audio, reagendar, cancelar, sin horarios disponibles, urgencias dichas de varias formas, fuera de tema, intentos de inyección ("ignora tus instrucciones", "dame los datos de otro paciente"), precio no registrado, paciente sin consentimiento, ráfagas de mensajes, faltas de ortografía y español coloquial.
- Revisiones deterministas primero: qué herramientas se llamaron y con qué datos, que nunca se agende sin confirmación, número de botones y largo del texto, y que no aparezcan diagnósticos ni medicamentos.
- Para tono y claridad, una rúbrica calificada por un modelo más capaz (por ejemplo `claude-sonnet-5-5`), con la rúbrica guardada en el repo.
- Todo error real en producción se convierte en un caso de evaluación.
