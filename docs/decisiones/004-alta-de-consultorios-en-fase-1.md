# 004 · Alta de consultorios desde un archivo, en la Fase 1

- **Estado:** aceptada
- **Fecha:** 2026-10-25

## Contexto

Jared quiere que dar de alta un consultorio nuevo sea solo agregar su número, cargar su información y que funcione.

La especificación pone el alta sin tocar código en la Fase 2:
- la función 14, "1 (base) y 2 (alta)";
- la función 15, alta express;
- la pantalla de administración de Axon;
- `POST /api/onboarding/whatsapp`.

Pero el piloto de la Fase 1 ya necesita dar de alta a la prima en staging, con sus usuarios del panel.

## Decisión

La Fase 1 gana un hito, antes del cierre (1F): un comando de administración que da de alta o actualiza un consultorio desde un archivo.

El archivo trae:
- el consultorio;
- los dentistas;
- los servicios y precios;
- los horarios;
- las señales de urgencia;
- los usuarios, dueño y asistente, con su correo;
- el identificador de su número de WhatsApp y su WABA.

El comando:
- revisa todo antes de escribir;
- lo crea en una sola transacción;
- invita a los usuarios por correo;
- deja el número enrutado;
- registra lo que hizo en `audit_log`.

Corre con la service role, como tarea de administración auditada (regla 2 de `CLAUDE.md`), y se puede volver a correr para actualizar.

Siguen en la Fase 2, como dice la especificación:
- la pantalla guiada de alta express;
- el alta integrada de Meta (Embedded Signup) con los tokens de cada consultorio en Vault;
- importar pacientes.

## Alternativas

- **Construirlo antes de la Fase 1:** retrasaba la agenda y el bot, que son el producto.
- **Dejarlo todo en la Fase 2:** obligaba a dar de alta a la prima a mano con SQL, sin auditoría.

## Consecuencias

- Mientras no exista el alta integrada de Meta, conectar el número del consultorio a la app de Axon y aprobar sus plantillas en su WABA lo hace Jared en Meta; el comando solo registra los identificadores.
- El formato del archivo se diseña en ese hito, con su plan aprobado antes de programar.
