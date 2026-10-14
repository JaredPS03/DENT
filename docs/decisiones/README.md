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
| [001](001-cimientos.md) | Cimientos del repositorio: rol `axon_app`, RLS y llaves compuestas, webhook, CI y alertas | aceptada |
