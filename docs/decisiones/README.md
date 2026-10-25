# Decisiones de arquitectura

Cada decisión importante que se toma al construir queda aquí, en un archivo `NNN-titulo-corto.md` numerado en orden (`001-cimientos.md`, `002-proveedor-de-transcripcion.md`…). Sirven para que, meses después, tú o Claude sepan por qué el sistema es como es.

Las decisiones que ya venían en el plan están en la especificación (Tecnologías y "Decisiones técnicas del kit de construcción"); aquí van las que se tomen durante la construcción.

## Reglas

- Una decisión por archivo, corta: se lee en dos minutos.
- Nunca se borra. Si cambia, se escribe una nueva y la vieja queda como "Reemplazada por NNN".
- Si la decisión contradice la especificación, primero se actualiza la especificación, con tu aprobación.

## Plantilla

```markdown
# NNN · Título

- **Estado:** propuesta | aceptada | reemplazada por NNN
- **Fecha:** AAAA-MM-DD

## Contexto
Qué problema hay y qué lo limita: especificación, seguridad, costo o tiempo.

## Decisión
Qué se decidió, en una o dos frases.

## Alternativas
Qué más se consideró y por qué no se eligió.

## Consecuencias
Qué se gana, qué se pierde y qué hay que vigilar.
```

## Índice

| # | Decisión | Estado |
| --- | --- | --- |
| [001](001-cimientos.md) | Cimientos del repositorio: rol `axon_app`, RLS y llaves compuestas, webhook, CI y alertas | aceptada; en parte reemplazada por 002 |
| [002](002-endurecimiento-tras-revision.md) | Endurecimiento tras la revisión de seguridad: contenido mínimo, permisos, seed solo local, plazos, orden de llegada y CI | aceptada; en parte reemplazada por 003 |
| [003](003-revision-de-cierre.md) | Revisión de cierre de la Fase 0: seed atómico, `DATABASE_URL` con libpq, logs de asyncio, copias de `.env`, permisos y la puerta con el número de Axon | aceptada |
| [004](004-alta-de-consultorios-en-fase-1.md) | Alta de consultorios desde un archivo, como hito de la Fase 1 | aceptada |
| [005](005-sesiones-del-panel-y-agenda.md) | Sesiones del panel (JWKS, membresía, límite) y reglas de la agenda (15 min, 1 h de anticipación) | aceptada |
