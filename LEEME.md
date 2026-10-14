# Axon Dental · Kit para construir con Claude Code

Este kit convierte el plan de Axon Dental en instrucciones para que Claude Code construya el producto contigo, fase por fase, con seguridad, integridad y escala desde el primer commit.

No es un solo prompt gigante, por una razón práctica: Claude Code lee `CLAUDE.md` al inicio de cada sesión, y su documentación recomienda mantenerlo por debajo de 200 líneas, porque un archivo largo ocupa contexto y se sigue peor. Por eso lo permanente vive en `CLAUDE.md`, lo de cada área se carga solo cuando hace falta, y tú le das a Claude un hito a la vez.

## Qué trae

| Archivo | Qué es | Cuándo actúa |
| --- | --- | --- |
| `CLAUDE.md` | El prompt maestro: el producto, los lentes de experto con que decide, el stack, 20 reglas no negociables y cómo trabajar | En cada sesión, solo |
| `.claude/rules/` | Reglas por área: seguridad, base de datos, bot de IA, WhatsApp y panel | Al tocar archivos de esa área |
| `.claude/agents/` | Dos revisores con contexto propio: `revisor-seguridad` y `revisor-clinico` | Al cerrar un hito, o cuando se lo pidas |
| `.claude/settings.json` | Permisos: Claude no lee `.env` ni llaves, y te pregunta antes de `git push` o de tocar la base en la nube | Siempre |
| `docs/especificacion.md` | El plan completo; es la fuente de verdad | Claude lee la sección que toca |
| `docs/prompt-del-bot.md` | El prompt de sistema del asistente de WhatsApp | Lo usa el código del bot |
| `docs/avance.md` | Bitácora: qué quedó y qué sigue | Al empezar y al cerrar cada hito |
| `docs/decisiones/` | Decisiones de arquitectura, una por archivo | Cuando se decide algo nuevo |
| `prompts/fases.md` | Los prompts que tú pegas, uno por hito | Al iniciar cada hito |

## Antes de empezar

Instala Claude Code (instrucciones en code.claude.com/docs), Git, Docker Desktop (Supabase lo necesita para correr en tu computadora), Python 3.12 con uv, Node.js 20 o más con pnpm, y la CLI de Supabase. Crea también una llave de la API de Claude en su consola para el bot y sus evaluaciones: se cobra por uso, aparte de tu plan.

## Cómo empezar

1. Descomprime el kit: la carpeta `axon-dental` será tu repositorio. Si la mueves o copias, incluye la carpeta oculta `.claude`. Entra en ella y corre `git init`.
2. Abre una terminal en esa carpeta y corre `claude`. Escribe `/context` para confirmar que cargó `CLAUDE.md`.
3. Activa el modo plan (Shift+Tab hasta que la terminal lo indique) y pega el prompt de la Fase 0 de `prompts/fases.md`.
4. Responde la entrevista, revisa el plan y apruébalo. Al final, revisa la evidencia que te muestre y haz commit.

## Cómo trabajar cada hito

- Un hito por sesión y `/clear` entre hitos: un contexto limpio da mejores resultados que una sesión eterna.
- Tú apruebas el plan antes del código. Corregir un plan cuesta minutos; corregir código, horas.
- No aceptes "ya quedó" sin evidencia: salida de pruebas, capturas o el reporte de los revisores.
- Si Claude se equivoca dos veces en lo mismo, `/clear` y vuelve a empezar con un prompt más preciso.
- Lo que solo dijiste en el chat, la siguiente sesión no lo sabe. Si decides algo, pide que quede en la especificación, en `CLAUDE.md` o en `docs/decisiones/`.
- Cada error real del bot se vuelve un caso de evaluación, para que no se repita.

## Lo que Claude Code no hace por ti

- Verificar tu negocio en Meta, dar de alta el número de tu prima con coexistencia y el de Axon, y conseguir que Meta apruebe las plantillas.
- Revisar con un abogado el aviso de privacidad, el contrato de encargo y la excepción de urgencias antes del consentimiento.
- Buscar y registrar la marca Axon en el IMPI.
- Conseguir el acceso a la API de Dentalink.
- Validar con tu prima las señales de urgencia y los textos del bot.
- Probar con pacientes reales y decidir cuándo pasar a producción.

## Cuida los datos

- Nunca pegues en Claude Code conversaciones, nombres ni teléfonos reales de pacientes: usa los datos de prueba del seed.
- Los secretos van en `.env` y en los secretos de GitHub y del hosting; nunca en el código. Claude no puede leer ni crear `.env`: tú copias `.env.example` y pones los valores.
- El bloqueo de `settings.json` es una barrera, no una garantía: un script que Claude ejecute sí podría abrir un archivo. Revisa los comandos antes de aprobarlos y no guardes en tu computadora secretos de producción que no necesites.

## Ajustes opcionales

- Los revisores usan el mismo modelo que tu sesión (`model: inherit`). Si quieres que la revisión de seguridad use siempre el modelo más capaz de tu plan, cambia esa línea en `.claude/agents/revisor-seguridad.md`, por ejemplo a `opus`.
- Las evaluaciones del bot llaman a la API real: cuestan poco por corrida, pero no son gratis. Córrelas cuando cambie el bot y antes de desplegar, no en cada prueba.
