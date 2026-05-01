# Flujos conversacionales y demo

## Eventos Evolution (webhook)

El backend acepta `POST /webhooks/evolution` con cuerpo típico:

- `event`: `messages.upsert` o `MESSAGES_UPSERT` (según versión).
- `data.key.remoteJid`: chat origen (solo 1:1; grupos `@g.us` se ignoran).
- `data.key.fromMe`: si es `true`, se ignora (evita bucles).
- Texto en `data.message.conversation`, `extendedTextMessage.text`, o caption de imagen.

Lista blanca: `EVOLUTION_MY_PHONE` y/o `EVOLUTION_ALLOWED_JID(S)`; ver [README](../README.md).

**NEKOBOT / self-chat:** `EVOLUTION_SELF_CHAT_MODE` (`only` por defecto) usa `sender` del webhook vs `remoteJid` para saber si es *Mensajes contigo*. Los `fromMe` solo cuentan ahí; el eco del bot se ignora por **id de mensaje** tras `sendText` (como `providerMessageId` en NEKOBOT).

## Flujo inicial (todos)

1. Primer mensaje (ej. *hola*): el bot se presenta como **RutaPe** y pide elegir **1** o **2**.
2. Estado `await_role` hasta recibir una opción válida.

## Opción 1 — Persona que busca trabajo / oportunidades

- Se guarda `role = job_seeker`.
- Estados: `ask_region` → `ask_goal` (empleo / curso / voluntariado) → `ask_skills` → `ask_education` → `ready`.
- Comandos en `ready`:
  - `/oportunidades` — matching con todas las **Opportunity** activas (seed + publicadas por empleadores por WhatsApp).
  - `/perfil`, `/menu`, `/reset`.

## Opción 2 — Empleador

- Se guarda `role = employer`.
- Estados: `emp_company` (nombre org.) → `emp_need` (qué perfiles busca) → `ready`.
- Comandos en `ready`:
  - `/publicar` — crea una fila **Opportunity** (tipo, título, región, requisitos, URL), con `employer_wa_id` = tu JID.
  - `/candidatos` o `/empresa` — modo búsqueda: el **siguiente mensaje** es el prompt; se rankean **perfiles job_seeker en BD** + candidatos **seed**.
  - `/perfil`, `/menu`, `/reset`.

## Panel admin

- `GET /admin` — HTML con tablas que hacen poll a `GET /admin/api/snapshot`.
- Muestra perfiles, oportunidades y **flow_events** (transiciones y comandos relevantes).

## Guion de demo (3–5 min)

| Min | Acción | Resultado |
|-----|--------|-------------|
| 0:00 | Contexto: dos audiencias, un solo WhatsApp | — |
| 0:45 | Opción **1**, completar registro, `/oportunidades` | Lista de empleo/curso/voluntariado |
| 2:00 | `/reset`, opción **2**, nombre empresa + necesidad | Perfil empleador |
| 2:30 | `/publicar` y completar 5 pasos | Nueva fila en admin “Oportunidades” |
| 3:30 | `/candidatos` + prompt | Lista mezclada WhatsApp + seed |
| 4:30 | Mostrar `/admin` actualizándose | — |

## Checklist pre-demo WhatsApp

- [ ] Evolution conectado; instancia = `EVOLUTION_INSTANCE`.
- [ ] Webhook HTTPS público → `/webhooks/evolution`.
- [ ] `EVOLUTION_MY_PHONE` correcto; `EVOLUTION_API_KEY` coincide con Evolution.
- [ ] `EVOLUTION_WEBHOOK_TOKEN` vacío o alineado con lo que envía Evolution.
- [ ] Backend `uvicorn` desde `backend/` y `GET /health` OK.
- [ ] Opcional: `ADMIN_DASHBOARD_TOKEN` + abrir `/admin?token=...`.
