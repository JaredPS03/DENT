# Axon Dental · Plan técnico y de producto

Versión del 13 de octubre de 2026. Escrito para Jared, fundador de Axon; "tu prima" es la dentista del piloto. Es la fuente de verdad del producto: si el código o `CLAUDE.md` la contradicen, pregunta antes de seguir.

## Resumen

Axon Dental es un asistente de WhatsApp con IA que atiende, agenda y da seguimiento a los pacientes de consultorios dentales pequeños, y le demuestra al dentista cuánto dinero le recupera cada mes. Es un producto repetible: el mismo sistema, configurado para cada consultorio.

- **Cliente:** el dentista que trabaja solo o con una asistente, que hoy usa libreta y su WhatsApp personal. Nicho inicial: Oaxaca.
- **Promesa:** ningún paciente se queda sin respuesta y cada cita perdida se intenta recuperar.
- **Precio:** 1,500 MXN de instalación y 850 MXN al mes por consultorio (plan base; detalle en Costos y planes).
- **Piloto:** el consultorio de tu prima, en la Fase 1.
- **Construcción:** código propio sobre la API oficial de WhatsApp, en cinco fases (0 a 4) de unos 4 a 5 meses en total, trabajando solo (estimado).

**Posicionamiento:** no es un chatbot; es tu recepción por WhatsApp que te demuestra cada mes cuánto te recupera.

### Qué cambió en esta versión

Ajustes por el [análisis de competencia](https://claude.ai/code/artifact/e260377a-b3e4-4679-b5d0-aab0acca414c) del 13 de octubre de 2026:

- Las notas de voz pasan a la Fase 1, porque la IA de Meta no procesa audios.
- Un reporte mensual simple de dinero recuperado sale desde la Fase 2; el tablero completo sigue en la Fase 3.
- La agenda va detrás de un conector, para usar la de Axon, Google Calendar o Dentalink sin reescribir el bot.
- El paso a humano siempre lleva un resumen, algo que la IA de Meta no hace, y la urgencia se revisa antes que el aviso de privacidad para no frenar a un paciente con dolor.
- Proveedores de IA y transcripción que no entrenen con los datos de los pacientes.
- Seis secciones nuevas de especificación: flujo de conversación, plantillas de WhatsApp, pantallas, endpoints, requisitos técnicos y criterios de aceptación.

## Mercado y propuesta de valor

Agendar por WhatsApp ya no te distingue. Hay tres tipos de rivales: software dental que manda el WhatsApp a mano ([Dentalink](https://intercom.help/softwaredentalink/es/articles/9493137-como-notificar-cita-por-whatsapp), que ya anuncia un "Contact Center" con IA), chatbots dentales cobrados en dólares ([Aurora Inbox](https://www.aurorainbox.com/chatbot-clinicas-dentales-whatsapp/), US$99–179 al mes) y la IA de Meta dentro de WhatsApp Business, que es la amenaza mayor. El detalle está en el [análisis de competencia](https://claude.ai/code/artifact/e260377a-b3e4-4679-b5d0-aab0acca414c); Axon gana en seis huecos.

| Hueco | Qué hace hoy el mercado | Qué hace Axon |
| --- | --- | --- |
| Consultorio de un solo dentista | Software por usuario y con curva de aprendizaje (Dentalink); bots que dependen de Google Calendar (Aurora) | Alta en una tarde y en persona, sin computadora ni Google Calendar, precio en pesos |
| WhatsApp que de verdad agenda | Dentalink manda el WhatsApp a mano; la IA de Meta contesta con su catálogo, sin agenda real | Agenda propia o conectada a Dentalink; solo ofrece horarios libres |
| Audios, fotos y anticipo | La IA de Meta no procesa audios; nadie cobra anticipo | Transcribe audios desde la Fase 1; lee comprobantes y cobra anticipo |
| Seguimiento de tratamientos largos | Consejos de cuidado sueltos (Aurora); nadie avisa del abandono | Plan mes a mes para ortodoncia e implantes, con alerta de abandono |
| Demostrar el retorno | La IA de Meta no da métricas; Aurora no describe reportes | Reporte mensual en pesos desde la Fase 2 y tablero en la Fase 3 |
| Datos de salud protegidos | Con la IA de Meta, Meta puede usar los mensajes para mejorar su IA | Consentimiento expreso, contrato de encargo y proveedores que no entrenan con los datos |

La frase de venta que resume todo: *"Axon te cuesta 850 al mes y este mes te recuperó X pesos."*

## Fases de implementación

Son cinco fases de lo mínimo vendible a lo completo, y no pasas a la siguiente sin cumplir su puerta. Las duraciones son estimadas para una persona trabajando sola.

Fase 0 · Preparación (1–2 semanas) → Fase 1 · Núcleo (4–6) → Fase 2 · Vendible (4) → Fase 3 · Retención (4–5) → Fase 4 · Pluses 2 y 3 (4–5). Cada fase termina en una puerta, descrita abajo.

La Fase 1 es la de hoy: al cumplir su puerta ya tienes un producto probado con un consultorio real, listo para vender.

### Fase 0 · Preparación (1–2 semanas, en paralelo a la Fase 1)

- **Objetivo:** tener listos los trámites que tardan y no dependen del código.
- **Incluye:** cuenta de Meta Business y su verificación, cuenta de WhatsApp Business API, alta del número de tu prima con coexistencia, número propio de Axon para alertas y reportes, proyecto en Supabase, repositorio, aviso de privacidad, búsqueda de la marca Axon en el IMPI, plantillas de la Fase 1 enviadas a Meta, y dos respuestas: si tu prima usa Dentalink y si Dentalink da acceso a su API.
- **Puerta:** envías y recibes un mensaje de prueba por la API con el número real.

### Fase 1 · Núcleo con tu prima (4–6 semanas)

- **Objetivo:** que tu prima deje de contestar la agenda a mano.
- **Incluye:** agendar, reagendar y cancelar; disponibilidad en tiempo real; recordatorio 24 h antes con botones de confirmar o cambiar; bandeja de conversaciones con botón para tomar el control; consentimiento en el primer mensaje; escalado de urgencias con resumen para el dentista; notas de voz transcritas; panel con la agenda del día; configuración de horarios y servicios.
- **Clave técnica:** la base de datos nace multi-clínica y cada evento se registra desde el día uno, porque el reporte y el tablero de dinero recuperado se alimentan de ellos. La agenda va detrás de un conector, para que después pueda ser la de Axon, Google Calendar o Dentalink sin reescribir el bot.
- **Puerta:** 4 semanas seguidas operando sin citas dobles ni mensajes sin respuesta, y tu prima usa el panel a diario.

### Fase 2 · Producto vendible y consultorio pequeño (4 semanas)

- **Objetivo:** dar de alta a un consultorio nuevo en una tarde, sin que dependa de ti a diario.
- **Incluye:** Plus 1 completo (alta express, importar pacientes, resumen diario por WhatsApp, panel en el celular), reporte mensual simple de dinero recuperado, conexión opcional con Google Calendar o Dentalink, medidor de mensajes con topes por plan y cobro de tu mensualidad.
- **Puerta:** 3 consultorios pagando, además de tu prima, y cada uno con su primer reporte mensual recibido.

### Fase 3 · Retención y mega plus (4–5 semanas)

- **Objetivo:** que el sistema genere citas nuevas y lo demuestre en pesos.
- **Incluye:** recall semestral, reactivación de inactivos, solicitud de reseñas, lista de espera para llenar huecos, y el Plus 4 completo: tablero de dinero recuperado en el panel, con el detalle por tipo de recuperación.
- **Puerta:** cada cliente ve en el tablero al menos una cita recuperada por recall, reactivación o lista de espera.

### Fase 4 · Fotos, anticipos y tratamientos largos (4–5 semanas)

- **Objetivo:** cubrir los dos pluses de mayor valor por paciente.
- **Incluye:** el resto del Plus 2 (fotos, comprobantes y anticipo con Mercado Pago) y el Plus 3 (seguimiento mes a mes de ortodoncia e implantes).
- **Puerta:** al menos un consultorio cobrando anticipos y otro con pacientes de ortodoncia en seguimiento.

## Funcionalidades

Son 29 funciones repartidas en las fases; las 13 de la Fase 1 bastan para cobrarle a tu prima y operar un consultorio completo.

| # | Función | Qué hace | Datos que necesita | Fase |
| --- | --- | --- | --- | --- |
| 1 | Agendar cita | El paciente pide cita por texto o audio; el bot ofrece 2–3 horarios en botones y confirma | Servicios con duración, horarios, citas ocupadas, nombre y teléfono | 1 |
| 2 | Reagendar y cancelar | Mueve o cancela la cita y libera el espacio al instante | Cita existente, política de cancelación | 1 |
| 3 | Disponibilidad en tiempo real | Calcula huecos libres con horario, duración, descansos y bloqueos; nunca ofrece un horario ocupado | Horario semanal, excepciones (vacaciones), citas, margen entre citas | 1 |
| 4 | Recordatorio 24 h | Envía la plantilla de recordatorio el día anterior | Citas de mañana, plantilla aprobada por Meta | 1 |
| 5 | Confirmación con botones | Confirmo / Cambiar / Cancelar; actualiza el estado y avisa al consultorio | Respuesta del paciente, estado de la cita | 1 |
| 6 | Bandeja de conversaciones | Todos los chats en el panel, con audio y transcripción, quién habla (bot o humano) y la cita ligada | Mensajes, pacientes, estado de la conversación | 1 |
| 7 | Tomar el control | La asistente pausa el bot, lee un resumen de lo hablado, contesta ella y lo reactiva | Usuario del panel, estado de la conversación | 1 |
| 8 | Escalado de urgencias | Si detecta dolor fuerte, sangrado o hinchazón, responde un mensaje fijo y alerta al dentista con un resumen | Lista de señales de alerta validada por el dentista | 1 |
| 9 | Consentimiento | En el primer mensaje: aviso de privacidad simplificado y botón Acepto; guarda la prueba | Versión del aviso, fecha y hora, teléfono | 1 |
| 10 | Agenda del día | Vista de las citas de hoy con estado: confirmada, pendiente, asistió, faltó | Citas, estados | 1 |
| 11 | Configuración del consultorio | Horarios, servicios, duraciones, precios de referencia, dirección, formas de pago | Datos que da el dentista | 1 |
| 12 | Registro de eventos | Guarda cada hecho medible (cita agendada fuera de horario, confirmada, reagendada) | Eventos del sistema | 1 |
| 13 | Notas de voz | Transcribe el audio, lo atiende como texto y guarda audio y transcripción | Audio del paciente | 1 |
| 14 | Multi-clínica | Cada consultorio con sus datos separados y su número; alta de nuevos sin tocar código | Clínica, usuarios, número de WhatsApp | 1 (base) y 2 (alta) |
| 15 | Alta express | Asistente de configuración en una tarde: horario, servicios, precios, aviso de privacidad | Datos del consultorio | 2 |
| 16 | Importar pacientes | Desde contactos, una hoja de cálculo o fotos de la libreta leídas con IA y revisadas | Nombre, teléfono, última visita si existe | 2 |
| 17 | Resumen diario | Cada noche le manda al dentista por WhatsApp la agenda de mañana y lo pendiente | Citas, conversaciones escaladas | 2 |
| 18 | Reporte mensual simple | El día 1 le manda al dentista las citas confirmadas por recordatorio, agendadas fuera de horario y reagendadas, con su valor en pesos | Eventos, precios de servicios, asistencias | 2 |
| 19 | Conector de agenda externa | Lee y escribe citas en Google Calendar o en Dentalink si el dentista ya los usa | Permiso de Google; acceso a la API de Dentalink (por confirmar) | 2 (Google) y 2–3 (Dentalink) |
| 20 | Medidor de mensajes | Cuenta mensajes cobrables por consultorio y avisa al 80% y 100% del tope | Mensajes enviados y su categoría | 2 |
| 21 | Cobro de tu mensualidad | Cobra la suscripción de Axon y avisa si un pago falla | Plan del consultorio, método de pago | 2 |
| 22 | Recall semestral | Invita a limpieza a quien cumple 6 meses (configurable) de su última visita | Última visita, servicio, permiso de marketing | 3 |
| 23 | Reactivación de inactivos | Contacta a pacientes sin visita en 12 meses o más con una invitación | Historial de citas, permiso de marketing | 3 |
| 24 | Solicitud de reseñas | Tras una cita atendida pregunta cómo le fue y manda a todos el enlace de reseñas de Google; si la respuesta es Regular o Mal, además avisa al dentista. Google prohíbe pedir reseñas solo a quien quedó contento | Cita con asistió, enlace de reseñas | 3 |
| 25 | Lista de espera | Si alguien cancela, ofrece el hueco a quien lo esperaba | Lista de espera por servicio y horario | 3 |
| 26 | Tablero de dinero recuperado | Convierte eventos en pesos y los muestra por mes y por tipo de recuperación (Plus 4) | Eventos, precios de servicios, asistencias | 3 |
| 27 | Fotos y comprobantes | Adjunta la foto al chat, hace preguntas de triage y lee comprobantes de pago | Imagen del paciente | 4 |
| 28 | Anticipo | Manda un enlace de Mercado Pago; si no se paga en el plazo, libera el horario (Plus 2) | Cuenta de Mercado Pago del dentista, servicios con anticipo | 4 |
| 29 | Seguimiento post-tratamiento | Plan mes a mes para ortodoncia e implantes, revisión del día siguiente y alerta de abandono (Plus 3) | Planes de tratamiento | 4 |

## Los cuatro diferenciadores

Los cuatro entran al producto; el Plus 4 es el que evita que te cancelen, así que sus datos se registran desde la Fase 1 y el primer reporte sale en la Fase 2.

### Plus 1 · Hecho para el consultorio pequeño (Fase 2)

El dentista que trabaja solo no tiene recepcionista, ni computadora en el sillón, ni tiempo para configurar software. Aurora Inbox también le vende, pero depende de Google Calendar y cobra en dólares; Axon no le pide ninguna de las dos cosas.

- **El bot es la recepción.** Contesta, agenda y confirma sin que nadie abra el panel.
- **Alta en una tarde, contigo en persona.** Tú capturas horario, servicios y precios; el dentista solo aprueba.
- **Pacientes desde la libreta.** Le tomas foto a la libreta o exportas sus contactos; la IA extrae nombre, teléfono y última visita, y el dentista revisa antes de guardar.
- **Resumen diario por WhatsApp.** Cada noche: "Mañana tienes 6 citas, 5 confirmadas; 1 paciente pidió hablar contigo". Con botones para resolver desde ahí.
- **Panel en el celular.** Se instala como app (PWA), sin tiendas de aplicaciones.
- **Sigue usando su WhatsApp.** Con coexistencia, la app del teléfono y el bot comparten el mismo número.
- **Cómo se vende:** "No tienes que aprender nada: tu WhatsApp ahora contesta solo".

### Plus 2 · Voz, fotos y anticipo (voz en Fase 1; fotos y anticipo en Fase 4)

En México los pacientes mandan audios y fotos, y apartan con transferencia. La IA de Meta no procesa audios: es tu diferencia más fácil de mostrar en una demo.

- **Notas de voz:** el sistema descarga el audio, lo transcribe a texto en español y la IA lo atiende como cualquier mensaje. En la bandeja se ven el audio y su transcripción.
- **Fotos del diente:** la IA no diagnostica. Adjunta la foto al chat, pregunta lo básico (dónde duele, del 1 al 10, desde cuándo, si hay hinchazón o fiebre) y, si hay señales de alerta, pasa a humano con un resumen para el dentista.
- **Comprobantes:** si la foto es un comprobante de transferencia, lee monto, fecha y referencia y lo deja pendiente de que el consultorio lo apruebe.
- **Anticipo:** para los servicios que el dentista elija, el bot manda un enlace de pago de Mercado Pago. Al pagarse, la cita queda apartada; si no se paga en el plazo configurado, el horario se libera. El dinero cae directo en la cuenta del dentista: Axon nunca toca el dinero de los pacientes.
- **Cómo se vende:** "Si tu paciente apartó con anticipo, ya no falta".

### Plus 3 · Seguimiento de ortodoncia e implantes (Fase 4)

Son los pacientes que más dinero dejan y los que más abandonan a medio tratamiento.

- **Plan de tratamiento por paciente:** el dentista elige una plantilla (ortodoncia, implante) y ajusta frecuencias y etapas. Axon no inventa protocolos clínicos.
- **Control automático:** agenda o recuerda el siguiente ajuste según el plan.
- **Revisión del día siguiente:** "¿Cómo amaneciste después de tu procedimiento?" con botones Bien, Molestia, Necesito ayuda. "Necesito ayuda" escala al dentista.
- **Cuidados aprobados:** solo envía los textos de cuidados que el dentista escribió o aprobó.
- **Alerta de abandono:** avisa al dentista cuando un paciente lleva más semanas de las previstas sin su siguiente cita.
- **Cómo se vende:** "Ningún paciente de ortodoncia se te pierde a medio tratamiento".

### Plus 4 · Tablero de dinero recuperado, el mega plus (registro en Fase 1, reporte en Fase 2, tablero en Fase 3)

El dentista ve en pesos lo que Axon le genera, y así deja de verlo como un gasto. Ningún competidor lo ofrece: la IA de Meta no da métricas y Aurora no describe reportes.

- **Qué cuenta como recuperado, con reglas estrictas:** citas agendadas con el consultorio cerrado, citas reagendadas desde el recordatorio en lugar de perderse, huecos llenados desde la lista de espera, pacientes de recall o reactivación que agendaron, y anticipos cobrados.
- **Cómo se convierte a pesos:** solo cuenta una cita cuando el paciente asistió, al precio del servicio que el dentista registró. Mejor quedarse corto que inflar: la confianza es lo que vendes.
- **Retorno:** "Axon te costó 850 y te recuperó X" en grande, con el detalle abajo.
- **Reporte mensual:** el día 1 de cada mes llega por WhatsApp una imagen con el resumen.
- **Para ti:** el mismo dato te avisa qué cliente tiene poco retorno y podría cancelar, y con permiso de cada consultorio se vuelve tu mejor testimonio de venta.

### Ventajas que no son una función

Estas cinco no se programan, pero pesan tanto como los pluses al cerrar una venta:

- **API oficial de WhatsApp:** los bots de freelancers con APIs no oficiales pueden hacer que baneen el número del dentista.
- **Soporte en persona en Oaxaca:** los demás venden y atienden a distancia.
- **Datos de salud protegidos:** contrato de encargo y proveedores que no entrenan con los mensajes, a diferencia de la IA de Meta.
- **Precio en pesos y por consultorio:** sin cobro por usuario ni sorpresas en dólares.
- **Complementa, no reemplaza:** convive con Dentalink (expediente) y Doctoralia (pacientes nuevos).

## Tecnologías

Todo es código propio sobre servicios estándar: lo difícil de copiar es tu lógica para dentistas, no las piezas.

| Capa | Tecnología | Por qué |
| --- | --- | --- |
| Canal | API oficial de WhatsApp de Meta (Cloud API), conexión directa, con coexistencia | Sin riesgo de baneo como las APIs no oficiales; sin cargo extra de intermediarios; el dentista sigue usando su app con el mismo número. Para dar de alta números de clientes necesitarás el registro de proveedor de tecnología de Meta y su alta integrada (Embedded Signup); confírmalo en la Fase 0 |
| Backend | Python + FastAPI | Es tu terreno como ingeniero de IA; asíncrono para recibir los webhooks de Meta rápido; validación de datos con Pydantic |
| Cola de trabajos | Cola en Postgres (pgmq de Supabase) o Redis con un worker | El webhook responde a Meta al instante y la IA trabaja aparte; si algo falla, el trabajo se reintenta sin perder el mensaje |
| IA conversacional | Modelo rápido con llamadas a funciones, por ejemplo Claude Haiku 5.5 | La IA no toca la base de datos: decide qué función llamar y tu código valida y ejecuta. Así nunca inventa horarios. Elige un proveedor cuyo contrato diga que no entrena con tus datos |
| Imágenes | El mismo modelo, que también lee imágenes | Clasifica la foto (diente, comprobante, otro) y lee comprobantes; no diagnostica |
| Voz | Un servicio de transcripción en español (por ejemplo, Whisper) | Convierte audios en texto en segundos y a bajo costo. Va desde la Fase 1 porque la IA de Meta no procesa audios |
| Base de datos | Supabase (PostgreSQL) con seguridad por fila | Cada tabla lleva el consultorio y la seguridad por fila impide ver datos de otro. Incluye autenticación del panel, almacenamiento privado de audios y fotos, y tiempo real para la bandeja |
| Agenda | Propia en Postgres, detrás de un conector; Google Calendar o Dentalink opcionales | Una restricción de exclusión en la tabla de citas hace imposible guardar dos citas encimadas, aunque lleguen al mismo segundo. Si la agenda vive en Axon, el consultorio ya no se va. El conector es una interfaz única (buscar huecos, crear, mover, cancelar) con una implementación por fuente, así el bot no cambia si la agenda está en Dentalink |
| Panel web | Next.js + Tailwind, como app instalable (PWA) | Rápido de construir, funciona en el celular y se actualiza en vivo con Supabase |
| Tareas programadas | Programador del worker o pg\_cron | Recordatorios, recall, reactivación, reseñas, resumen diario y reporte mensual |
| Pagos | Mercado Pago, con la cuenta de cada consultorio | El más usado en México; el dinero va directo al dentista y Axon no maneja fondos de terceros. Tu propia mensualidad también puede cobrarse con suscripciones de Mercado Pago |
| Hosting | Backend en Railway o Render; panel en Vercel | Despliegue sencillo y barato al inicio; se puede mover después |
| Monitoreo | Sentry y registros de cada conversación | Te enteras de un error antes que el dentista y puedes revisar qué contestó el bot |

**Por qué no n8n para este producto:** su licencia sí permite usarlo como motor si tus clientes nunca tocan n8n ([FAQ de licencia](https://docs.n8n.io/n8n-community-license/community-license/license-faq.md)), pero para un producto con muchos consultorios necesitas pruebas automáticas, control de versiones y separación estricta de datos, y eso es más sólido en código. n8n queda para tus sistemas a medida.

## Arquitectura

```text
Paciente ⇄ WhatsApp Cloud API (Meta)
                 │ webhook con firma validada
                 ▼
 [Webhook FastAPI] ──► [Cola pgmq] ──► [Worker: orquestador del bot] ⇄ IA (Claude) y transcripción
                                              │ funciones validadas por el código
 [Programador: recordatorios y reportes] ─────┤
                                              ▼
 Dentista y asistente ─► [Panel Next.js] ─► [Servicios del negocio] ─► [Supabase: Postgres con RLS, Storage, Realtime]
                                              │
                 Conectores opcionales: Google Calendar, Dentalink, Mercado Pago
```

Todo mensaje entra por Meta, espera en la cola y lo atiende el orquestador; ninguna acción llega a la base de datos sin pasar por los servicios del negocio.

**Un mensaje de punta a punta:**

1. El paciente escribe o manda un audio; Meta avisa al webhook.
2. El webhook guarda el mensaje, le responde a Meta en el acto y lo pone en la cola.
3. El worker toma el trabajo: si es audio lo transcribe; si es imagen la clasifica.
4. Si la conversación está en modo humano, solo aparece en la bandeja y el bot no contesta.
5. Si no, el orquestador junta el contexto (datos del consultorio, citas del paciente, últimos mensajes) y llama a la IA.
6. La IA pide funciones, como buscar disponibilidad; tu código valida y ejecuta cada una y le devuelve el resultado.
7. La IA redacta una sola respuesta con botones; sale por Meta y se registran el mensaje, su categoría de cobro y los eventos para el tablero.
8. El panel del consultorio se actualiza en tiempo real.

## Modelo de datos

Toda tabla lleva `clinic_id` y una regla de seguridad por fila: un consultorio nunca puede leer datos de otro, aunque haya un error en el código.

| Tabla | Para qué | Campos clave | Fase |
| --- | --- | --- | --- |
| `clinics` | El consultorio | nombre, dirección, zona horaria, número de WhatsApp, `waba_id`, plan, tope de mensajes, bot activo (`bot_enabled`) | 1 |
| `clinic_users` | Dentista y asistente | usuario, rol (dueño, asistente), teléfono para alertas | 1 |
| `providers` | Quién atiende: dentista o sillón | nombre, activo; al inicio uno por consultorio | 1 |
| `services` | Catálogo | nombre, duración en minutos, precio de referencia, requiere anticipo, monto del anticipo | 1 |
| `schedules` | Horario semanal | día, hora de inicio y fin, descansos, margen entre citas | 1 |
| `schedule_exceptions` | Vacaciones y bloqueos | fecha y hora de inicio y fin, motivo | 1 |
| `patients` | Pacientes | nombre, teléfono, última visita, permiso de marketing, origen (bot, importado, Dentalink), id externo | 1 |
| `consents` | Prueba del consentimiento | paciente, versión del aviso, fecha y hora, mensaje en que aceptó | 1 |
| `appointments` | Citas | paciente, servicio, `provider_id`, inicio y fin, estado (pendiente, confirmada, asistió, faltó, cancelada), creada por (bot, humano), id externo si viene de Google o Dentalink; restricción de exclusión contra traslapes por `provider_id` | 1 |
| `conversations` | Un chat por paciente | estado (bot, humano, cerrada), última actividad, ventana de 24 h abierta hasta | 1 |
| `messages` | Cada mensaje | dirección, tipo (texto, audio, imagen, botón), contenido, transcripción, id de Meta, categoría cobrable | 1 |
| `escalations` | Pases a humano | motivo, prioridad (urgente, normal), resumen, quién lo atendió | 1 |
| `events` | Hechos para el tablero | tipo (agendada fuera de horario, confirmada, reagendada, reactivada…), cita, fecha, valor en pesos cuando el paciente asiste | 1 |
| `templates` | Plantillas de Meta | nombre, categoría (utilidad, marketing), idioma, estado de aprobación | 1 |
| `audit_log` | Bitácora | quién (usuario o sistema), acción, tabla e id, cambios, fecha | 1 |
| `webhook_events` | Avisos crudos de Meta, para reprocesar si algo falla | id de Meta, tipo, contenido, recibido, procesado; el contenido se borra a los 7 días de procesado | 1 |
| `message_usage` | Medidor del tope | mes, mensajes por categoría, costo estimado | 2 |
| `agenda_connections` | Conector de agenda | fuente (axon, google, dentalink), credenciales cifradas, última sincronización, estado | 2 |
| `monthly_reports` | Reporte mensual | mes, citas recuperadas por tipo, total en pesos, fecha de envío | 2 |
| `subscriptions` | Tu cobro a cada consultorio | plan, precio, estado de pago, próximo cobro | 2 |
| `waitlist` | Lista de espera | paciente, servicio, rango de fechas preferido | 3 |
| `campaigns` | Recall y reactivación | tipo, regla (meses sin visita), plantilla, enviados, agendados | 3 |
| `payments` | Anticipos | cita, monto, enlace de pago, estado, id de Mercado Pago o comprobante | 4 |
| `treatment_plans` | Ortodoncia e implantes | paciente, tipo, etapas, frecuencia de control, próxima fecha esperada | 4 |
| `followups` | Seguimientos del plan | plan, fecha, respuesta del paciente, alerta | 4 |

## Funciones que la IA puede llamar

La IA solo actúa a través de estas funciones; el consultorio y el paciente los pone tu código desde la sesión, nunca la IA, así que no puede tocar datos de otro consultorio.

| Función | Qué hace | Parámetros | Regla | Fase |
| --- | --- | --- | --- | --- |
| `buscar_disponibilidad` | Devuelve horarios libres | servicio, desde, hasta, turno preferido | La IA solo puede ofrecer horarios que devolvió esta función | 1 |
| `agendar_cita` | Crea la cita | servicio, inicio | Solo después de que el paciente confirme el horario; la base rechaza traslapes | 1 |
| `reagendar_cita` | Mueve una cita | cita, nuevo inicio | Solo citas del mismo paciente | 1 |
| `cancelar_cita` | Cancela y libera el horario | cita, motivo | Respeta la política de cancelación del consultorio | 1 |
| `consultar_mis_citas` | Lista las citas próximas del paciente | ninguno | Solo del paciente que escribe | 1 |
| `info_consultorio` | Horarios, dirección, servicios, precios de referencia, formas de pago | tema | Si un dato no está registrado, dice que no lo sabe y ofrece pasar con el consultorio | 1 |
| `registrar_paciente` | Crea o completa al paciente | nombre | El teléfono sale de WhatsApp, no se lo pregunta | 1 |
| `escalar_a_humano` | Pausa el bot y alerta al consultorio | motivo, prioridad, resumen | Obligatoria ante dolor fuerte, sangrado, hinchazón, quejas o dudas clínicas | 1 |
| `agregar_lista_espera` | Anota al paciente para un hueco | servicio, rango de fechas | Solo si no hay horario que le acomode | 3 |
| `crear_enlace_anticipo` | Genera el enlace de pago | cita | Solo para servicios marcados con anticipo | 4 |
| `registrar_comprobante` | Guarda un comprobante leído de una foto | cita, monto, fecha, referencia | Queda pendiente hasta que el consultorio lo aprueba | 4 |
| `responder_seguimiento` | Guarda la respuesta a un seguimiento | plan, respuesta | Si el paciente pide ayuda o reporta molestia fuerte, escala | 4 |

**Reglas fijas del bot** (en sus instrucciones y comprobadas en código):

1. Se presenta como asistente virtual del consultorio desde el primer mensaje.
2. Nunca da diagnósticos, ni recomienda medicamentos, ni interpreta una foto clínicamente.
3. Nunca inventa horarios, precios ni datos del consultorio.
4. Si le preguntan algo fuera del consultorio, lo dice con amabilidad y vuelve al tema.
5. Ante cualquier señal de urgencia, escala; si hay dificultad para respirar o tragar, indica llamar al 911.
6. Contesta en un solo mensaje completo y usa botones o listas para elegir, porque cada mensaje cuesta.
7. Si llega un audio, contesta a lo que dice el audio; nunca le pide al paciente que mejor escriba.
8. Al pasar a humano deja un resumen de tres líneas: quién es, qué quiere y qué se intentó.

## Flujo de conversación

1. Llega un mensaje: texto, audio, imagen o botón.
2. Si es audio, se transcribe.
3. ¿El bot está en pausa (modo humano)? Sí: solo aparece en la bandeja y el bot no contesta.
4. ¿Hay señal de urgencia? Sí: mensaje fijo y alerta al dentista con resumen; si le cuesta respirar o tragar, indicar el 911. El flujo termina aquí.
5. ¿Aceptó el aviso de privacidad? No: se manda el aviso con el botón Acepto y no se guardan síntomas.
6. La IA entiende qué quiere: agendar o cambiar, pregunta del consultorio, o fuera de tema.
7. Agendar: `buscar_disponibilidad`. Si hay horario, ofrece 2–3 en botones y, al confirmar, `agendar_cita` y aviso de confirmación. Si no hay, lista de espera (Fase 3) o pasa a humano si urge.
8. Pregunta del consultorio: contesta con los datos registrados. Fuera de tema: responde con amabilidad y regresa al tema.

El orden importa: la urgencia se revisa antes que el aviso, así que un paciente con dolor fuerte recibe ayuda aunque todavía no haya aceptado, y nunca termina en una lista de horarios. Cada paso deja un evento registrado, que alimenta el reporte de dinero recuperado; una imagen sigue el mismo camino y, si es un comprobante, queda pendiente de aprobar (Fase 4).

## Plantillas de WhatsApp

Todo mensaje que inicia el consultorio fuera de la ventana de 24 h necesita una plantilla aprobada por Meta; estas 14 cubren las cinco fases, y las cinco de la Fase 1 se envían a aprobar en la Fase 0.

| Plantilla | Categoría | Texto propuesto | Botones | Fase |
| --- | --- | --- | --- | --- |
| `recordatorio_cita` | Utilidad | Hola {{1}}, te recordamos tu cita de {{2}} mañana {{3}} a las {{4}} en {{5}}. | Confirmo · Cambiar · Cancelar | 1 |
| `cita_confirmada` | Utilidad | Tu cita de {{1}} quedó el {{2}} a las {{3}}. Dirección: {{4}}. | Ver ubicación (enlace) | 1 |
| `cita_movida` | Utilidad | El consultorio movió tu cita de {{1}} al {{2}} a las {{3}}. ¿Te queda bien? | Sí · Elegir otro horario | 1 |
| `retomar_conversacion` | Utilidad | Hola {{1}}, te escribimos de {{2}} sobre tu mensaje. ¿Seguimos? | Sí, seguimos | 1 |
| `alerta_equipo` (de Axon al equipo del consultorio) | Utilidad | Hola {{1}}, un paciente de {{2}} necesita al equipo (prioridad {{3}}). Ábrelo desde el botón. | Abrir conversación (enlace) | 1 |
| `resumen_diario` (al dentista) | Utilidad | Mañana tienes {{1}} citas, {{2}} confirmadas. {{3}} pacientes pidieron hablar contigo. | Ver agenda (enlace) | 2 |
| `reporte_mensual` (de Axon al dentista) | Utilidad | En {{1}}, Axon te recuperó {{2}} citas, unos {{3}} pesos. | Ver detalle (enlace) | 2 |
| `recall_limpieza` | Marketing | Hola {{1}}, ya pasaron {{2}} meses de tu última limpieza en {{3}}. ¿Te agendo? | Agendar · Más adelante · No recibir más | 3 |
| `reactivacion` | Marketing | Hola {{1}}, hace tiempo no te vemos en {{2}}. Tenemos horarios esta semana. | Agendar · No recibir más | 3 |
| `hueco_disponible` | Utilidad | Se liberó un horario el {{1}} a las {{2}} para {{3}}. ¿Lo quieres? | Lo quiero · No, gracias | 3 |
| `pedir_resena` | Marketing | ¿Cómo te fue en tu cita de hoy en {{1}}? | Muy bien · Regular · Mal | 3 |
| `enlace_anticipo` | Utilidad | Para apartar tu cita del {{1}} se pide un anticipo de {{2}}. Puedes pagarlo aquí. | Pagar (enlace) | 4 |
| `revision_dia_siguiente` | Utilidad | Hola {{1}}, ¿cómo te sientes después de tu {{2}}? | Bien · Molestia · Necesito ayuda | 4 |
| `control_tratamiento` | Utilidad | Te toca tu control de {{1}}. ¿Te agendo? | Agendar · Llamar al consultorio | 4 |

Meta revisa cada plantilla y puede cambiar su categoría. Las de marketing solo van a pacientes que dieron permiso. El mensaje de consentimiento y las respuestas dentro de las 24 h no necesitan plantilla.

## Pantallas del panel

El panel se diseña primero para el celular, porque el dentista lo abre entre paciente y paciente; la Fase 1 necesita seis pantallas.

| Pantalla | Qué muestra | Qué permite | Fase |
| --- | --- | --- | --- |
| Entrar | Acceso con correo y enlace mágico | Entrar sin contraseña | 1 |
| Hoy | Citas del día con estado y chats que necesitan a un humano | Marcar asistió o faltó, abrir el chat de un paciente | 1 |
| Agenda | Vista por día y por semana | Crear, mover y cancelar citas; bloquear horarios | 1 |
| Conversaciones | Lista de chats con filtro "necesita humano", audio con transcripción y el resumen del bot | Tomar el control, contestar, reactivar el bot | 1 |
| Paciente | Datos, citas, consentimiento y permiso de marketing | Editar, exportar o borrar sus datos (derechos ARCO) | 1 |
| Configuración | Horarios, servicios, precios, señales de urgencia, textos del bot | Cambiar todo sin pedirte ayuda | 1 |
| Alta express | Pasos guiados para un consultorio nuevo | Dejarlo listo en una tarde | 2 |
| Reporte | Dinero recuperado del mes y del año | Compartir el reporte | 2 (simple) y 3 (tablero) |
| Uso y plan | Mensajes usados contra el tope, plan y pagos a Axon | Comprar un paquete extra | 2 |
| Campañas | Recall y reactivación con resultados | Programar y pausar campañas | 3 |
| Lista de espera | Pacientes esperando un hueco | Ofrecer un hueco a mano | 3 |
| Tratamientos | Planes de ortodoncia e implantes y alertas de abandono | Crear planes desde una plantilla | 4 |
| Pagos | Anticipos y comprobantes por aprobar | Aprobar o rechazar un comprobante | 4 |
| Administración de Axon (solo tú) | Todos los consultorios, consumo, errores y retorno de cada uno | Dar de alta, pausar y revisar quién podría cancelar | 2 |

## Endpoints del backend

El panel lee directo de Supabase con seguridad por fila; el backend solo expone lo que valida reglas o habla con servicios externos. Todo lo que empieza con `/api` exige la sesión de Supabase y toma el consultorio del token, nunca del cuerpo de la petición.

| Método | Ruta | Para qué | Fase |
| --- | --- | --- | --- |
| GET | `/webhooks/whatsapp` | Verificación del webhook con Meta | 0 |
| POST | `/webhooks/whatsapp` | Recibe mensajes y estados de entrega; valida la firma de Meta, descarta duplicados por id de mensaje y encola | 1 |
| GET | `/health` | Monitoreo de base de datos, cola y conexión con Meta | 1 |
| POST | `/api/citas` | Crear una cita desde el panel con las mismas reglas que el bot | 1 |
| PATCH | `/api/citas/{id}` | Mover, cancelar o marcar asistió o faltó; registra el evento | 1 |
| POST | `/api/conversaciones/{id}/tomar` | Pausar el bot en un chat | 1 |
| POST | `/api/conversaciones/{id}/soltar` | Reactivar el bot | 1 |
| POST | `/api/conversaciones/{id}/mensajes` | Enviar un mensaje como humano; usa `retomar_conversacion` si la ventana de 24 h ya cerró | 1 |
| GET | `/api/pacientes/{id}/exportar` | Exportar todo lo del paciente (ARCO) | 1 |
| DELETE | `/api/pacientes/{id}` | Borrar al paciente con sus audios y fotos (ARCO) | 1 |
| GET | `/api/medios/{id}` | URL firmada de corta vida para un audio o foto; cada acceso queda en la bitácora | 1 |
| POST | `/api/onboarding/whatsapp` | Recibir el alta integrada de Meta y guardar el número del consultorio | 2 |
| POST | `/api/pacientes/importar` | Importar desde hoja de cálculo o fotos de la libreta | 2 |
| POST | `/api/agenda/conectar/{fuente}` | Conectar Google Calendar o Dentalink | 2 |
| POST | `/webhooks/google-calendar` | Avisos de cambios en Google Calendar | 2 |
| GET | `/api/reportes/{mes}` | Datos del reporte de dinero recuperado | 2 |
| POST | `/webhooks/mercadopago` | Avisos de pago de tu mensualidad y de los anticipos | 2 y 4 |
| POST | `/api/comprobantes/{id}/aprobar` | Aprobar un comprobante leído de una foto | 4 |

**Tareas programadas** (corren en el worker, no son endpoints públicos): recordatorios cada 15 minutos, resumen diario a las 20:00, medidor de mensajes cada hora, recall y reactivación una vez al día, y reporte mensual el día 1 a las 9:00, todo en hora del centro de México.

## Requisitos no funcionales

Son las metas que el sistema cumple sin que nadie las vea; si una falla, el dentista lo nota antes que tú.

| Requisito | Meta | Cómo se cumple |
| --- | --- | --- |
| Respuesta a Meta | El webhook contesta en menos de 1 s | Todo lo pesado va a la cola |
| Respuesta al paciente | Menos de 10 s con texto y 20 s con audio, en 9 de cada 10 mensajes | Modelo rápido, contexto corto, una sola respuesta |
| Citas dobles | Cero | Restricción de exclusión en la tabla de citas |
| Mensajes perdidos o repetidos | Cero | Id de mensaje de Meta único y reintentos de la cola |
| Disponibilidad | 99.5% al mes (máximo unas 3.6 h caído) | Hosting administrado, monitoreo y alertas |
| Separación de consultorios | Ninguno ve datos de otro | Seguridad por fila y pruebas automáticas que intentan romperla |
| Seguridad | Datos cifrados en tránsito y en reposo | Tokens de Meta, Google y Dentalink cifrados; llaves solo en el servidor; bitácora de accesos |
| Respaldos | Diarios, con una prueba de restauración al mes | Respaldos de Supabase |
| Escala | 50 consultorios sin cambiar la arquitectura | Más workers detrás de la misma cola |
| Costo variable | Menos del 40% de la mensualidad | Medidor de mensajes y topes por plan |
| Hora | Todo en hora del centro de México, sin horario de verano | Zona `America/Mexico_City` en citas y tareas |
| Monitoreo | Te enteras antes que el dentista | Alerta a tu WhatsApp si un mensaje lleva 5 min sin respuesta, si falla un recordatorio o si baja la calidad del número |
| Pruebas | Nada sale sin probarse | 50 conversaciones simuladas (agendar, audio, urgencia, fuera de tema) antes de cada despliegue |

## Criterios de aceptación de la Fase 1

La Fase 1 está terminada cuando cada punto pasa con tu prima y con conversaciones de prueba; hasta entonces no le vendes a otro consultorio.

- [ ] Un paciente agenda de principio a fin por texto en menos de 2 minutos.
- [ ] Un paciente agenda mandando solo audios.
- [ ] El bot nunca ofrece un horario ocupado ni fuera del horario del consultorio, en 50 conversaciones simuladas.
- [ ] Si dos pacientes piden el mismo horario al mismo tiempo, solo uno lo obtiene y al otro se le ofrecen alternativas.
- [ ] El recordatorio sale 24 h antes y cada botón (Confirmo, Cambiar, Cancelar) actualiza la cita.
- [ ] "Me duele mucho" pasa a humano en el primer mensaje y llega con resumen.
- [ ] Cuando la asistente toma el control, el bot no contesta hasta que ella lo suelta.
- [ ] Sin aceptar el aviso de privacidad, el bot no guarda síntomas, salvo para alertar una urgencia.
- [ ] Una pregunta fuera del tema recibe una respuesta amable que regresa al consultorio.
- [ ] Cada evento medible queda registrado con su tipo y su cita.
- [ ] Tu prima usa el panel desde su celular sin ayuda.
- [ ] Cuatro semanas seguidas sin citas dobles ni mensajes sin respuesta (la puerta de la fase).

## Cumplimiento

Axon Dental cumple con Meta porque es un bot de un negocio concreto (citas y atención), y con la ley porque pide consentimiento expreso antes de tratar datos de salud. Esto no sustituye la revisión de un abogado para tu aviso de privacidad y tus contratos.

### Política de WhatsApp (Meta)

- **Permitido:** bots de un negocio para citas, recordatorios, atención y ventas. **Prohibido:** asistentes de IA "para todo", desde el 15 de enero de 2026 para cuentas existentes ([respond.io](https://respond.io/blog/whatsapp-chatbot-policy-2026)). Por eso el bot se mantiene en el tema del consultorio.
- **Plantillas:** todo mensaje que inicia el consultorio (recordatorio, recall, reactivación) debe ser una plantilla aprobada por Meta, en su categoría correcta.
- **Permiso de marketing:** recall y reactivación son marketing; solo se envían a pacientes que lo aceptaron, y cada mensaje permite darse de baja.
- **Calidad del número:** si muchos pacientes bloquean o reportan, Meta limita el número. Pocos mensajes de marketing y siempre útiles.

### Datos personales y de salud (México)

- **Ley vigente:** la nueva Ley Federal de Protección de Datos Personales en Posesión de los Particulares, vigente desde el 21 de marzo de 2025; la autoridad es la Secretaría Anticorrupción y Buen Gobierno, que sustituyó al INAI ([Garrigues](https://www.garrigues.com/es_ES/noticia/mexico-nueva-ley-federal-proteccion-datos-personales-posesion-particulares-introduce)).
- **Datos sensibles:** los datos de salud lo son. El primer mensaje muestra un aviso de privacidad simplificado con enlace al integral y pide un botón Acepto; se guarda versión, fecha, hora y mensaje como prueba. Sin aceptar, el bot solo da información general y no guarda síntomas. La excepción es una urgencia: el bot alerta al dentista aunque el paciente no haya aceptado todavía; confirma este punto con tu abogado al redactar el aviso.
- **Quién es quién:** el dentista es el responsable de los datos; Axon es el encargado que los trata por su cuenta. Firma con cada consultorio un contrato de encargo que diga para qué usas los datos y cómo los proteges.
- **Terceros:** el aviso debe mencionar a los servicios que procesan mensajes (Meta, el proveedor de IA, Supabase, el de transcripción, y Google o Dentalink si el consultorio los conecta). Elige proveedores de IA y transcripción cuyo contrato diga que no entrenan con esos datos: es una ventaja frente a la IA de Meta, que puede usar los mensajes para mejorarse.
- **Derechos ARCO:** acceso, rectificación, cancelación y oposición. El panel debe poder exportar o borrar todo lo de un paciente.
- **No es expediente clínico:** Axon guarda agenda y conversaciones, no notas clínicas ni diagnósticos.

### Seguridad mínima

- Audios y fotos en almacenamiento privado, con enlaces temporales para verlos.
- Seguridad por fila en todas las tablas y llaves secretas solo en el servidor.
- Bitácora de quién vio o cambió qué en el panel.
- Política de conservación: define con cada dentista cuánto tiempo se guardan los audios y fotos.

## Costos y planes

Un consultorio típico te cuesta unos 340 MXN al mes y te paga 850, así que te deja unos 510 MXN (60%) antes de tus costos fijos. Son estimaciones: mídelas con tu prima en la Fase 1.

**Supuestos:** 120 citas al mes, unos 1,000 mensajes cobrables de servicio o utilidad y 100 de marketing; tipo de cambio de 18.5 MXN por dólar. Desde el 1 de octubre de 2026 Meta cobra también las respuestas del negocio; en México, unos US$0.0085 por mensaje de servicio o utilidad ([Mercately](https://blog.mercately.com/?p=30248), [Vonage](https://api.support.vonage.com/hc/en-us/articles/29982160922268-WhatsApp-Service-Messages-Become-Billable-on-Oct-1-2026-What-You-Need-to-Know)) y US$0.0436 por marketing ([Aurora Inbox](https://www.aurorainbox.com/2026/05/26/precio-whatsapp-business-api-mexico-2026/)). Los mensajes que manda el paciente no se cobran.

| Concepto por consultorio al mes | Cálculo | MXN |
| --- | --- | --- |
| WhatsApp servicio y utilidad | 1,000 × 0.157 | 157 |
| WhatsApp marketing (recall, reactivación) | 100 × 0.807 | 81 |
| IA conversacional | estimado | 90 |
| Transcripción de audios | estimado | 10 |
| **Costo variable** |  | **338** |
| **Margen sobre 850** | 850 − 338 | **512** |

**Costos fijos (aproximados, verifica precios vigentes):** Supabase, servidor y monitoreo, unos 830 MXN al mes en total. Con 2 consultorios ya los cubres. Con 10 consultorios te quedan unos 4,290 MXN al mes; con 30, unos 14,000, más 1,500 de instalación por cada alta.

### Planes propuestos

| Plan | Precio | Incluye | Mensajes incluidos al mes |
| --- | --- | --- | --- |
| Esencial | 850 MXN/mes + 1,500 instalación | Fases 1 a 3: agenda, recordatorios, bandeja, voz, resumen diario, reporte y tablero de dinero recuperado, recall, reseñas y conexión con Google Calendar o Dentalink | 1,000 de servicio y utilidad, 100 de marketing |
| Completo (sugerido) | 1,350 MXN/mes + 1,500 instalación | Todo lo anterior más anticipos, fotos y comprobantes, seguimiento de ortodoncia e implantes y reactivación ampliada | 1,500 de servicio y utilidad, 300 de marketing |
| Paquete extra | 199 MXN | Se activa al llegar al tope, con aviso previo al 80% | +500 de servicio y utilidad |
| Paquete marketing | 149 MXN | Campañas extra de recall o reactivación | +100 de marketing |

El reporte y el tablero de dinero recuperado van en todos los planes: son lo que evita cancelaciones. Tu prima, a 500 MXN, te deja unos 160 de margen, suficiente para un piloto.

**Contra la competencia:** Aurora Inbox cobra US$99–179 al mes, Dentalink desde US$29 por usuario y Leadsales US$83.99; con 850 MXN por consultorio quedas muy por debajo. Mantén ese precio para arrancar y súbelo cuando tengas 5 testimonios con su reporte de dinero recuperado.

**Para que cada mensaje rinda:** el bot responde en un solo mensaje completo, usa botones y listas en lugar de preguntas abiertas, y los recordatorios piden la confirmación en el mismo mensaje.

## Riesgos y próximos pasos

El riesgo mayor no es técnico: es que el bot se equivoque con una cita real. Por eso la Fase 1 dura lo necesario con tu prima antes de vender.

| Riesgo | Qué pasaría | Cómo lo cubres |
| --- | --- | --- |
| El bot agenda mal | Paciente molesto, dentista desconfía | Horarios solo desde la función de disponibilidad, restricción contra traslapes, confirmación con botones y revisión diaria de conversaciones al inicio |
| Meta cambia precios o reglas | Se reduce tu margen o se limita una función | Topes por plan, paquetes de excedente y revisión mensual de las tarifas de Meta |
| Baja la calidad del número | Meta limita los envíos | Pocos mensajes de marketing, solo con permiso y siempre con opción de baja |
| Fuga de datos de salud | Problema legal y de reputación | Seguridad por fila, almacenamiento privado, contrato de encargo y aviso de privacidad revisado por un abogado |
| Todo depende de ti | Si te enfermas, nadie atiende fallas | Monitoreo con alertas, manual de soporte y, al crecer, ayuda pagada |
| La marca Axon está ocupada | Cambio de nombre después de invertir | Búsqueda en Marcanet del IMPI en la Fase 0 |
| La IA de Meta empieza a agendar bien | El dentista siente que ya lo tiene dentro de WhatsApp | Competir en lo que no hace: audios, recordatorios, recall, anticipos, reporte en pesos y datos protegidos; revisar cada mes qué agrega |
| Dentalink activa su Contact Center con IA | Sus clientes no buscan otra herramienta | Ofrecer la conexión con Dentalink y ganar con soporte en persona y el reporte de dinero recuperado |

### Esta semana

- [ ] Crear la cuenta de Meta Business e iniciar su verificación
- [ ] Revisar en la documentación de Meta el registro como proveedor de tecnología y la coexistencia
- [ ] Buscar "Axon" en Marcanet del IMPI
- [ ] Preguntarle a tu prima si usa Dentalink, qué plan tiene y qué es lo que más le molesta
- [ ] Escribirle a Dentalink para pedir acceso a su API
- [ ] Pedirle a tu prima su horario, servicios, duraciones y precios de referencia
- [ ] Crear el proyecto en Supabase con las tablas de la Fase 1
- [ ] Validar con tu prima la lista de señales de urgencia
- [ ] Redactar el aviso de privacidad simplificado e integral
- [ ] Dar de alta el número propio de Axon, del que salen las alertas al equipo y los reportes
- [ ] Enviar a Meta las 5 plantillas de la Fase 1

### Fuentes

- [Precios de WhatsApp Business Platform — Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Mensajes de servicio cobrables desde el 1 de octubre de 2026 — Vonage](https://api.support.vonage.com/hc/en-us/articles/29982160922268-WhatsApp-Service-Messages-Become-Billable-on-Oct-1-2026-What-You-Need-to-Know)
- [Nuevos precios y tarifa en México — Mercately](https://blog.mercately.com/?p=30248)
- [Precio de la API de WhatsApp en México 2026 — Aurora Inbox](https://www.aurorainbox.com/2026/05/26/precio-whatsapp-business-api-mexico-2026/)
- [Chatbot de WhatsApp para clínicas dentales — Aurora Inbox](https://www.aurorainbox.com/chatbot-clinicas-dentales-whatsapp/)
- [Política de chatbots de WhatsApp 2026 — respond.io](https://respond.io/blog/whatsapp-chatbot-policy-2026)
- [Política de precios para proveedores de IA — Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers/)
- [Coexistencia de WhatsApp — 360dialog](https://docs.360dialog.com/partner/onboarding/whatsapp-coexistence)
- [Nueva ley de datos personales en México — Garrigues](https://www.garrigues.com/es_ES/noticia/mexico-nueva-ley-federal-proteccion-datos-personales-posesion-particulares-introduce)
- [FAQ de licencia de n8n](https://docs.n8n.io/n8n-community-license/community-license/license-faq.md)

Las fuentes sobre Dentalink, Doctoralia, AgendaPro, Aurora Inbox y la IA de Meta están en el [análisis de competencia](https://claude.ai/code/artifact/e260377a-b3e4-4679-b5d0-aab0acca414c).

## Decisiones técnicas del kit de construcción

Estas decisiones completan la especificación para construirla con Claude Code; las reglas detalladas están en `CLAUDE.md` y `.claude/rules/`.

- **Quién atiende:** las citas llevan `provider_id` aunque el consultorio tenga un solo dentista, para crecer a varios dentistas o sillones sin migrar datos.
- **Rol de base de datos:** el backend usa el rol `axon_app` con RLS y fija el consultorio con `SET LOCAL app.clinic_id` en cada transacción; la service role solo se usa en migraciones y en tareas de administración auditadas.
- **El panel solo lee directo:** sus escrituras pasan por `/api` para validar reglas y registrar `audit_log`.
- **Orden por conversación:** un mensaje a la vez por conversación (lock) y agrupación de ráfagas del paciente unos segundos antes de llamar a la IA: responde una vez y ahorra mensajes cobrables.
- **Respuesta estructurada:** además de las funciones de la tabla, la IA usa una función interna `responder` (texto y, si aplica, botones, horarios o lista) para entregar su único mensaje; el código la valida contra los límites de WhatsApp.
- **Urgencia en dos capas:** un filtro determinista de palabras clave corre antes de la IA, y la IA también puede escalar.
- **Consentimiento en código:** la puerta del aviso la aplica el código, no el prompt.
- **Antes del consentimiento:** se guarda solo lo necesario para responder; si el paciente no acepta en 24 h, se borra el contenido de esos mensajes.
- **Modelo y datos:** el bot usa `claude-haiku-5-5` con herramientas en modo estricto (`strict: true`). En la API de Claude, el contenido de las conversaciones no se conserva por defecto y no se usa para entrenar sin permiso expreso; Fable 5.1 y Mythos 5.1 exigen 30 días de retención, así que no se usan con datos de pacientes. Nunca se ponen datos de pacientes dentro de los esquemas de las herramientas ([retención de datos](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention), [herramientas estrictas](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)).
- **Tope de mensajes:** al llegar al tope nunca se deja de contestar a un paciente ni de mandar recordatorios; el excedente se cobra. Lo que se detiene son las campañas de marketing.
- **Mensajes del dentista desde su app:** con coexistencia, si el consultorio contesta desde el teléfono, el bot se pausa en esa conversación por un tiempo configurable.
- **Lecturas sin consultorio fijado:** solo dos funciones `security definer` que devuelven ids: `private.clinic_for_phone_number`, para enrutar el webhook, y `private.active_clinic_ids`, para las tareas programadas. Todo lo demás se lee con el consultorio fijado.
- **Avisos crudos de Meta:** se guardan en `webhook_events` para poder reprocesarlos; la cola solo lleva ids, nunca el contenido de los mensajes.
- **Urgencia con texto fijo:** si la IA escala con prioridad urgente, el código manda el mismo mensaje fijo de urgencia que el filtro, revisado por el dentista.
- **Horarios validados en código:** la IA ofrece horarios por el id que devolvió `buscar_disponibilidad`, y el código rechaza cualquier id que no haya salido de ahí en esa conversación.
- **Alertas al equipo:** urgencias y escalados aparecen en el panel y llegan por WhatsApp desde el número de Axon con la plantilla `alerta_equipo`, que no lleva datos de salud: solo el enlace a la conversación.
- **Accesos registrados:** el panel lee directo de Supabase, pero lo sensible pasa por `/api` y queda en `audit_log`: abrir un audio o una foto, exportar o borrar a un paciente.
- **Interruptor por consultorio:** `clinics.bot_enabled` apaga el bot de un consultorio sin desplegar nada; sus mensajes siguen llegando a la bandeja.
- **Reseñas sin filtro:** el enlace de reseñas va a todos los pacientes atendidos, porque Google prohíbe pedirlas de forma selectiva ([política de Google](https://support.google.com/contributionpolicy/answer/7400114?hl=es)).
