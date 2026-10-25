# 005 · Sesiones del panel y reglas de la agenda (hito 1A)

- **Estado:** aceptada
- **Fecha:** 2026-10-25

## Contexto

`/api/citas` es el primer endpoint del panel. La especificación pide verificar la sesión de Supabase y tomar el consultorio de ella, nunca de la petición. `axon_app` solo ve `clinic_users` con un consultorio ya fijado.

La especificación no fija cada cuánto se ofrecen horarios ni con cuánta anticipación. Jared lo decidió al aprobar el plan del 1A.

## Decisión

**Sesiones**
- El token de acceso de Supabase se verifica con las llaves públicas del proyecto (JWKS en `SUPABASE_URL/auth/v1/.well-known/jwks.json`). Se comprueban:
  - la firma;
  - el vencimiento;
  - la audiencia `authenticated`;
  - el emisor;
  - el rol `authenticated`.
- Solo se aceptan llaves asimétricas (ES256 y RS256). Un token HS256 se rechaza, aunque esté firmado con el secreto local conocido de la CLI.
- Las llaves se guardan 10 minutos. Un id de llave nuevo provoca un refresco, como mucho cada 30 s.
- El consultorio y el rol salen de `private.clinic_memberships(user_id)`, una tercera función `security definer`. Como las otras dos, devuelve solo ids (y el rol), y solo `axon_app` puede llamarla; los consultorios pausados no dan acceso.
- Si un usuario pertenece a varios consultorios, elige uno con `X-Clinic-Id`, que solo vale si es suyo.
- El cuerpo de la petición no acepta `clinic_id`: un campo desconocido es 422.
- Límite de peticiones en `/api`, en memoria, que basta con una instancia del backend:
  - 60 por minuto por usuario con sesión;
  - 120 intentos rechazados (401) por minuto por dirección; las IPv6 cuentan por su /64.

  Una sesión válida nunca gasta el cupo de la dirección: detrás del proxy del hosting, todos los consultorios pueden llegar con la misma.
  - El limitador barre las llaves inactivas como mucho una vez por ventana. Con 10 000 llaves activas rechaza las nuevas, para que nadie lo vuelva lento rotando direcciones.
- Los cuerpos de `/api` tienen un tope de 16 KB. Un middleware responde 413 antes de leer el cuerpo, también si llega por partes sin `Content-Length`: FastAPI lee y parsea el cuerpo antes de verificar la sesión.

**Agenda**
- Horarios cada 15 minutos.
- El bot no ofrece nada que empiece en menos de 1 hora. El panel puede agendar cualquier horario futuro.
- Cada reserva bloquea a su dentista (`select … for update`) mientras revisa el hueco e inserta. Así el margen entre citas también se respeta con dos reservas simultáneas. La restricción de exclusión sigue como última garantía.
- Cada reserva bloquea también al paciente. Si el paciente ya tiene una cita activa que se encima, responde 409 "Ese paciente ya tiene una cita a esa hora.". Así un reintento nunca le da dos citas a la misma hora con dentistas distintos.
- Reagendar a la misma hora y con el mismo dentista no cambia nada ni registra otro evento.
- Una búsqueda de horarios abarca como mucho 31 días: la ventana puede venir de la IA.
- Un horario ocupado responde 409 "Ese horario ya no está disponible." con hasta 3 alternativas.
- Mover conserva la duración de la cita. Cancelar y marcar asistió o faltó solo se puede con citas pendientes o confirmadas. "Asistió" solo cuando ya pasó la hora, y registra el valor al precio de referencia del servicio.
- "Agendada fuera de horario" solo cuenta cuando agenda el bot con el consultorio cerrado: es la cita que se habría perdido.

## Alternativas

- **El consultorio en un *claim* del token (hook de Supabase):** más piezas que configurar, y el rol quedaría viejo hasta que el usuario volviera a entrar.
- **El consultorio en un encabezado, verificado después contra `clinic_users`:** también es seguro, pero hace que el cliente elija siempre; `X-Clinic-Id` queda solo para quien tiene varios consultorios.
- **Aceptar HS256 con el secreto del proyecto:** un secreto compartido más en el backend, y la puerta a confundir algoritmos.

## Consecuencias

- Variable nueva obligatoria: `SUPABASE_URL` (https fuera de desarrollo).
- **Para el panel local (1E):** la base local de la CLI debe firmar sesiones con llaves asimétricas, o `/api` las rechazará. Se configura al construir el panel.
- **Antes de escalar a varias instancias:** el límite en memoria se reemplaza por uno compartido o por el del proxy.
- **Condición para desplegar (1F):** uvicorn con `--forwarded-allow-ips` limitado al rango del proxy, nunca `*`. Si no, el límite por dirección cuenta la del proxy y un `X-Forwarded-For` falso no debe servir para esquivarlo.
- Cerrar sesión no invalida el token hasta que vence (1 h). Quitar a alguien de `clinic_users` sí le corta el acceso de inmediato.
- **Para el 1E:** CORS solo para el dominio del panel.
