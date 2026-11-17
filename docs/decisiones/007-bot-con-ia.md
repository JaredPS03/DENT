# 007 · El bot con IA: urgencias, consentimiento, herramientas, respaldo y evaluaciones (hito 1C)

- **Estado:** aceptada
- **Fecha:** 2026-11-17

## Contexto

El 1C reemplaza el bot de eco del 1B por el asistente con IA. La especificación y `.claude/rules/bot-ia.md` fijan el orden del turno, las herramientas y sus reglas. Dejaban abiertas tres cosas que Jared decidió al aprobar el plan:
- **El SDK oficial `anthropic`**, dependencia nueva aprobada.
- **Una urgencia con el chat en pausa o tomado** solo alerta al equipo, sin escribirle al paciente.
- **Sin "Acepto"**, el bot contesta dudas generales sin guardar síntomas y repite el aviso.

## Decisión

**El turno**, en este orden (`app/bot/orchestrator.py`):
1. **Bot apagado, en pausa o chat tomado:** el mensaje queda en la bandeja. Si trae una señal de urgencia, se abre un escalado urgente y se alerta al equipo, una vez mientras siga abierto (2 h).
2. **Filtro de urgencia determinista** (`app/bot/urgency.py`):
   - Toma la lista de señales del consultorio. Cada señal por defecto tiene reglas de frases cercanas ("me duele" cerca de "mucho"). Las señales que agrega el consultorio se buscan tal cual.
   - Perdona acentos, mayúsculas, letras repetidas y las confusiones comunes (h, z/s, c/s, v/b).
   - No dispara con preguntas sobre un procedimiento ("¿la limpieza duele?", "¿me va a doler la extracción?") ni con "si me duele…".
   - Si coincide: escalado urgente, mensaje fijo con el 911 y alerta. Va antes que el aviso de privacidad y que la IA.
3. **Puerta de consentimiento:**
   - Primer mensaje: el aviso con Acepto y No acepto.
   - **Acepto:** guarda la prueba en `consents` (versión del aviso y mensaje). El turno sigue y la IA contesta lo que el paciente había escrito.
   - **No acepto:** el chat pasa a una persona.
   - **Sin aceptar:** la IA solo da información general, sin agenda, y el código agrega el aviso y sus botones a su mensaje.
   - Lo que escribió quien no aceptó (texto, transcripciones y archivos) se borra a las 24 h. El trabajo de borrado vuelve por lo más reciente.
4. **La IA:** `claude-haiku-5-5`, sin `thinking` ni `effort`, o sea con los valores por defecto del modelo. El prompt sale de `docs/prompt-del-bot.md`, copiado a `app/bot/prompts/v1.0.md`; una prueba vigila que las dos copias coincidan.
   - **Orden para la caché:** herramientas, la parte fija del consultorio con la marca de caché, y al final la hora y el paciente.
   - **Lo que recibe:** los últimos 20 mensajes del último día y solo el primer nombre del paciente. El texto del paciente va siempre dentro de sus etiquetas, con `<` y `>` cambiados por caracteres parecidos.
5. **Herramientas** (`app/bot/tools.py`): las 8 de la Fase 1 más `responder`, en modo estricto, con todas las propiedades obligatorias.
   - Cada llamada se valida en código.
   - El consultorio y el paciente salen del turno.
   - Una cita solo se toca si es una cita próxima de ese paciente.
   - **Los horarios** de `buscar_disponibilidad` se guardan por conversación (`offered_slots`). `agendar_cita` y `reagendar_cita` solo aceptan un horario ofrecido en un turno anterior: el paciente tuvo que verlo y elegirlo, así que "nunca agendar sin confirmación" queda en código.
   - **`responder`** pone la etiqueta como título del botón y el inicio exacto, con zona, como id. Rechaza enlaces que no sean del consultorio. Con más de 3 opciones arma una lista.
6. **Respaldo:**
   - Topes: 6 llamadas a herramientas, 25 s por llamada al modelo y 60 s por turno.
   - Si la API falla, el modelo se niega, la respuesta sale cortada, termina dos veces sin `responder`, pasa del tope o se acaba el tiempo, se manda el mensaje fijo de respaldo y la conversación pasa a una persona con un resumen generado por el código.
   - Una falla de nuestros sistemas reintenta el turno. En el último intento también termina en el respaldo.
7. **Alertas al equipo** (`app/jobs/bot_jobs.py`):
   - Es la plantilla `alerta_equipo` desde el número propio de Axon (`AXON_PHONE_NUMBER_ID`), a cada usuario con teléfono de alertas.
   - Lleva el nombre del usuario, el del consultorio, la prioridad y el enlace a la conversación; nunca datos de salud.
   - Sale como máximo una vez por persona: se marca antes de llamar a Meta (`team_alerts`).
8. **Registro:** cada turno queda en `bot_turns` con tokens (incluida la caché), latencia, herramientas, modelo, versión del prompt, cómo terminó y el costo estimado (precios en `app/bot/costs.py`). Cada mensaje del bot guarda su versión del prompt.
9. **El cliente fija la URL de la API de Anthropic.** El SDK seguiría `ANTHROPIC_BASE_URL`, y los mensajes de pacientes solo deben ir a Anthropic.
10. **Evaluaciones** (`backend/tests/evals/`):
    - 84 casos en YAML. Cada uno corre el turno real contra la base local, con Meta simulado y el reloj real; los casos no dependen de fechas fijas.
    - Revisiones en código, y rúbrica de tono y claridad calificada por `claude-sonnet-5-5`.
    - Urgencias, inyección y dudas clínicas no pueden fallar.

## Ajustes tras las revisiones

- **revisor-seguridad** (1 alto, 5 medios, 5 bajos, todos corregidos con su prueba):
  - **El borrado a las 24 h** solo toca a quien nunca aceptó ninguna versión del aviso. Bloquea la fila del paciente y anula las filas antes de borrar los archivos.
  - **Horarios:** se anota cuándo se mostró cada uno (`offered_slots.shown_at`). Solo se agenda un horario que el paciente vio en un turno anterior, no uno que la IA buscó y no enseñó.
  - **Escalados:** uno por turno y prioridad (`escalations.turn_key`). El chat pasa a una persona al cerrar el turno, junto con la respuesta, así que un turno que falla después de escalar se reintenta completo. Una urgencia que llega después de un escalado normal abre el suyo.
  - **Sin consentimiento,** el escalado guarda un resumen escrito por el código, sin texto de la IA.
  - **Enlaces:** también los dominios sueltos ("bit.ly/…") pasan por la lista blanca.
  - **Botón Acepto:** lleva la versión del aviso, así que un toque en un aviso viejo no cuenta.
  - **Alertas:** si a alguien le quitaron el teléfono para alertas, su alerta se marca como fallida y no frena las demás.
  - **Urgencia repetida:** con el bot activo, una urgencia nueva siempre alerta.
  - **Variables del prompt:** se llenan en una sola pasada, para que un `{{` en los datos del consultorio no deje a todos sin respuesta.
- **revisor-clinico:**
  - **Filtro de urgencia:**
    - ahora reconoce cuando le pasa a otra persona ("le duele", "no le para de sangrar", "se pegó"), la garganta que se cierra, la lengua y los labios hinchados, y más formas de decir el dolor;
    - la hinchazón se reconoce por la raíz de la palabra;
    - ya no dispara con la temperatura de los alimentos ni con la muela del juicio o el primer diente que salen.
  - **Mensajes fijos:**
    - la urgencia manda al 911 solo si cuesta respirar o tragar;
    - el aviso dice para qué se usan los datos y pone el enlace en su propia línea;
    - el respaldo ya no promete "en un momento".
  - **Nota de voz sin transcribir:** pasa al equipo con un mensaje propio, porque podría ser una urgencia.
  - **Resúmenes del escalado:** dicen si al paciente se le contestó.
  - **Alerta repetida con el chat en pausa:** si la señal es nueva, o si es de respirar o tragar, se alerta otra vez.
  - **Prompt:**
    - prohíbe tranquilizar sobre síntomas, dar cuidados o remedios, nombrar medicamentos y prometer que algo no duele;
    - reconoce urgencias de otra persona;
    - acepta un horario elegido con palabras;
    - en los ejemplos usa los datos del consultorio, no nombres fijos.
  - **Evaluaciones:** 19 casos nuevos (82 en total) y más revisiones en código.

### Segunda ronda: decisiones de Jared y revisiones

- **Decisiones de Jared:**
  - **Señal de respirar o tragar:** fija. El consultorio no puede quitarla y se revisa antes que las demás (`active_signals`), también en la lista que ve la IA.
  - **Dudas clínicas:** primero se ofrece una valoración; se escala solo si el paciente quiere que le conteste alguien, o si hay urgencia. Cambió la especificación, la regla 10 del prompt y la descripción de `escalar_a_humano`.
  - **No acepto:** "Entendido. El asistente ya no te escribirá; alguien del equipo de {consultorio} te responderá por aquí." El chat pasa al equipo, ahora con un escalado y su alerta, porque el mensaje promete una respuesta. Falta la revisión del abogado.
  - **911 con el chat en pausa:** se queda como está (solo alerta al equipo).
- **revisor-seguridad:**
  - **Respirar o tragar con el chat en pausa:** vuelve a alertar después de 10 minutos, no con cada mensaje.
  - **Horarios mostrados:** un horario cuenta como visto solo si el mensaje que lo mostró llegó a WhatsApp (`offered_slots.shown_in_message_id`). Si ese mensaje falló, no se puede agendar.
  - **No acepto:** el chat pasa al equipo al cerrar el turno, junto con la respuesta, como los demás escalados.
  - **Diente que se salió:** "nuevo" o "nueva" ya no apagan la regla ("soy paciente nuevo, se me salió un diente" sí dispara).
  - **Enlaces:** el nombre de un archivo dentro de un enlace del consultorio ("aviso.pdf") y un título pegado a un nombre ("Dra.Laura") ya no se toman por dominios.
- **revisor-clinico:**
  - **Horarios:** la IA ve la fecha completa de cada horario, porque una etiqueta de ayer ("Mañana 10:30 am") puede ser hoy.
  - **Filtro de urgencia:**
    - "se cayó", "se pegó" y "me lastimé" no disparan si hablan de resinas, calzas o brackets;
    - "golpe" se reconoce por su raíz;
    - el diente que se salió exige la forma con "se" ("se me salió"; "me salió" es uno que brota);
    - más frases de respirar en tercera persona ("no le entra el aire", "se está ahogando");
    - una "bolita" en el párpado, el labio o la lengua ya no dispara; hinchados, sí.
  - **Sin consentimiento,** una urgencia que encuentra la IA le dice al equipo que es urgencia, sin síntomas.
  - **Prompt:** la regla 8 aclara que lo de no poner síntomas es solo sin consentimiento; fotos y archivos no se prometen revisar; tras "Acepto", una nota de voz sin transcribir pasa al equipo.
  - **Resúmenes:** "Llámale o escríbele ya" en urgencias sin respuesta, y "Quiere: no se sabe" en la nota de voz.
  - **Evaluaciones:** 2 casos nuevos (84 en total), revisa que se agende justo el horario elegido y la rúbrica ignora los textos fijos.
- **La versión del prompt sigue en 1.0:** ningún paciente ni evaluación ha usado una versión anterior. Desde el primer despliegue, cada cambio sube la versión.

## Alternativas

- **Herramientas distintas sin consentimiento:** cambiaría el inicio de la petición y rompería la caché. Se mandan siempre las mismas y el código rechaza las de agenda.
- **Confiar en el prompt para no agendar sin confirmación:** se descartó; la regla vive en código con los horarios ofrecidos.
- **Una tabla de reglas de urgencia editable por el consultorio:** se pospone. Hoy el consultorio activa o quita señales y agrega frases propias.
- **Llamadas HTTP directas en lugar del SDK:** más código propio que mantener; Jared eligió el SDK.

## Consecuencias

- **Dependencia nueva aprobada:** `anthropic`. PyYAML, que usan las evaluaciones, ya llega con `fastapi[standard]`; falta decidir si se declara aparte.
- **Variables nuevas:** `ANTHROPIC_API_KEY`, `BOT_MODEL`, `BOT_PROMPT_VERSION` y `AXON_PHONE_NUMBER_ID`. Se quitó `ECHO_BOT_ENABLED`.
- **Arranque del worker:** fuera de desarrollo no arranca sin llave o sin el prompt de la versión configurada. En desarrollo, sin llave filtra urgencias y pide el consentimiento, y lo demás queda en la bandeja.
- **Antes de pacientes reales:**
  - aprobar `alerta_equipo` en Meta con un botón de enlace al panel (el sufijo es el id de la conversación);
  - revisar con un abogado el aviso de privacidad y el borrado a las 24 h;
  - correr las evaluaciones con la llave.
- **Para hitos siguientes:**
  - **1D:** tope diario de transcripciones por consultorio, avisos de mensajes sin respuesta y limpieza de los horarios ofrecidos viejos.
  - **1E:** que el dentista ajuste los mensajes fijos y vea los escalados en el panel.
