---
paths:
  - "panel/**"
---

# Panel (Next.js)

## Para quién

El dentista lo abre en el celular entre un paciente y otro; la asistente, en la recepción. Cada pantalla resuelve una tarea en pocos toques.

## Reglas

- Diseña primero para 360 px de ancho, con áreas táctiles de al menos 44 px. Instalable como PWA.
- Lee directo de Supabase con la sesión del usuario (RLS); toda escritura va al `/api` del backend con su token.
- Sesión con enlace mágico y cookies (SSR de Supabase). Nada de datos de pacientes en `localStorage`, URLs ni títulos de página.
- Bandeja en tiempo real con Supabase Realtime. Audios y fotos con URLs firmadas de vida corta, pedidas a `GET /api/medios/{id}` para que cada acceso quede registrado.
- Las horas se muestran siempre en la zona del consultorio.
- Estados vacíos, de carga y de error con textos en español, sin tecnicismos.
- Accesible: etiquetas en los campos, contraste suficiente y navegación con teclado.

## Marca Axon

- Colores: azul noche `#0B1736` (texto y logo), azul eléctrico `#2E6BFF` (acentos y botones principales), niebla `#EEF1F6` (fondos suaves) y blanco.
- Tipografía: Lexend, seminegrita para títulos y regular para texto.
- Tono: claro y cálido; habla de pacientes, citas y pesos, no de "registros" ni "entidades".
