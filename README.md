# Agente WhatsApp — jóvenes y empresas (MVP hackathon)

Centraliza oportunidades (empleo, cursos, voluntariado) y conversa por **WhatsApp** (Evolution) con dos flujos: **persona que busca trabajo** y **empleador** (publicar ofertas y buscar candidatos). Hay un **panel admin** web para ver perfiles, oportunidades y el historial de flujo.

Documentación: [docs/VISION.md](docs/VISION.md), [docs/FLOWS.md](docs/FLOWS.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md).

## Requisitos

- Docker y Docker Compose (para Evolution + Redis + Postgres del stack Evolution).
- Python 3.11+ (backend).
- URL pública para webhooks (ngrok, Cloudflare Tunnel, etc.) si Evolution corre fuera de tu máquina o el webhook debe ser alcanzable desde internet.

## Backend (desarrollo local)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy ..\env.example ..\.env
# Edita la raíz .env (o crea backend\.env para overrides)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Tras un reinicio del PC: abre **Docker Desktop** y, en la raíz del repo, **`.\scripts\start-dev.ps1`** (levanta `docker compose` y luego uvicorn), o manualmente `docker compose up -d` + uvicorn como arriba. Opcional: **`.\scripts\validate-stack.ps1`** (Docker, puertos 8080/8000, claves Evolution alineadas).

Pruebas mínimas (desde `backend` con el venv activado): `python -m unittest discover -s tests -v`

Usa el mismo `PORT` que en tu `.env` si quieres alinearlo:

```powershell
$env:PORT=8000; uvicorn app.main:app --reload --host 0.0.0.0 --port $env:PORT
```

- Salud: `GET {APP_BASE_URL}/health` (por defecto `http://localhost:8000/health`)
- **QR de Evolution en el navegador:** `GET http://localhost:8000/setup/evolution-qr` — la app llama a tu Evolution (`GET /instance/connect/{EVOLUTION_INSTANCE}`) y muestra el QR para que lo escanee tu teléfono. No es “un QR del asistente”: vincula **tu WhatsApp** a **tu servidor Evolution**. Si usas `ADMIN_DASHBOARD_TOKEN`, añade `?token=...` (o header `X-Admin-Token`) igual que en `/admin`.
- **Diagnóstico si no hay QR:** `GET http://localhost:8000/setup/evolution-status` (mismo `?token=` si aplica) — indica si la URL base de Evolution responde y el `connectionState` de la instancia. Si el error dice que **no hay conexión** a `http://localhost:8080`, levanta el stack: en la raíz del repo ejecuta `docker compose up -d` y espera a que el contenedor `bcp_evolution_api` esté arriba; comprueba con `docker ps` que el puerto **8080** esté mapeado.
- **QR sale "Not Found" o instancia inexistente:** suele ser `EVOLUTION_INSTANCE` distinto al nombre real en Evolution o uvicorn sin reiniciar tras editar `.env`. El mensaje detallado viene de Evolution en `response.message`. Si ves `count: 0` sin imagen, el `docker-compose.yml` fija `CONFIG_SESSION_PHONE_VERSION` (WhatsApp Web cambia versión); recrea API: `docker compose up -d --force-recreate evolution-api`.
- **ngrok (webhook desde internet):** `ngrok config add-authtoken TU_TOKEN` (no lo guardes en el repo). Tunel al **puerto del backend** (8000 por defecto), no al 80: `.\scripts\ngrok-backend.ps1` o `ngrok http 8000`. Luego actualiza el webhook de la instancia en Evolution a `https://TU_SUBDOMINIO.ngrok-free.app/webhooks/evolution` (mismo cuerpo que en `POST /webhook/set/{instance}`).
- Webhook Evolution: `POST http://localhost:8000/webhooks/evolution`
- Búsqueda candidatos (API): `POST http://localhost:8000/company/search` con JSON `{"prompt": "..."}`
- **Admin:** `GET http://localhost:8000/admin` (vista HTML; datos en `GET /admin/api/snapshot`)

## Hablar con el agente por WhatsApp

1. Levanta Evolution (Docker o tu instalación) y conecta tu WhatsApp con QR (desde el panel de Evolution **o** abriendo en el navegador `http://localhost:8000/setup/evolution-qr` con el backend y `.env` apuntando a la misma instancia).
2. En Evolution, crea la instancia con el mismo nombre que `EVOLUTION_INSTANCE` en tu `.env`.
3. Pon **`EVOLUTION_MY_PHONE`** con tu número en dígitos (código país + celular).
4. Pon **`EVOLUTION_API_KEY`** igual a la API key que usa Evolution para llamar a `sendText`.
5. Expón el backend a internet (**ngrok**, **Cloudflare Tunnel**, etc.) y configura el **webhook** de la instancia a `https://TU_TUNEL/webhooks/evolution` con eventos de mensajes entrantes (`MESSAGES_UPSERT` / `messages.upsert`).
6. Si configuraste **`EVOLUTION_WEBHOOK_TOKEN`**, Evolution debe enviar ese valor en header `apikey` o compatible; si no, déjalo vacío en local.
7. **Solo tu chat (NEKOBOT / Evolution):** con **`EVOLUTION_SELF_CHAT_MODE=only`** (por defecto) solo se procesan conversaciones **contigo mismo** (*Mensajes contigo*): se detecta comparando `remoteJid` con el campo **`sender`** del JSON del webhook (igual que `packages/providers/src/evolution-api/normalizer.ts` en NEKOBOT). Los mensajes **`fromMe`** de otros contextos se ignoran; el **eco** de lo que envía el bot se filtra por **id de mensaje** devuelto por `sendText`. Si tu Evolution no manda `sender`, con un solo número en lista blanca se usa un fallback razonable.
8. Arranca `uvicorn` desde la carpeta `backend` y escribe **hola** en *Mensajes contigo*: el bot ofrece **1** o **2**. Detalle en [docs/FLOWS.md](docs/FLOWS.md).

### WhatsApp Desktop en la laptop y Evolution (que no “se vincule dos veces”)

- **Evolution no es un segundo WhatsApp en la laptop**: es una sesión **dispositivo vinculado** (como WhatsApp Web) asociada a tu número. El QR de Evolution lo escanea el **teléfono**, no la app de escritorio.
- **WhatsApp Desktop** (Microsoft Store / instalador) es **otro** dispositivo vinculado distinto. Desde el móvil: *Ajustes → Dispositivos vinculados* verás **Evolution** y **Escritorio** por separado; no son la misma fila, pero WhatsApp limita cuántos enlaces activos tolera y puede pedir **volver a vincular** si algo invalida la sesión.
- **Lo que sí se “mezcla” y rompe sesiones:** dos servidores **Evolution distintos** (p. ej. NEKOBOT en Docker y BCP en Docker) intentando usar **el mismo número** a la vez. Solo puede mandar uno la sesión tipo Web; el otro desconecta o provoca re-enlaces. Para este proyecto, deja **apagado** el compose del otro repo o usa **otro número** de prueba.
- **Reconexiones de Evolution** (caídas de Redis/Postgres, `docker compose restart`, versión de WhatsApp Web) pueden hacer que **otros dispositivos vinculados** (incluido Escritorio) pidan de nuevo el código. Es comportamiento del ecosistema WhatsApp + cliente no oficial, no del backend FastAPI.
- **Práctica estable:** para probar el bot usa **móvil** (*Mensajes contigo*) + **una sola** instancia Evolution. Si necesitas **Desktop** para trabajo, vincúlalo desde el móvil como siempre; si al desarrollar ves cierres o “vincular otra vez”, cierra Desktop mientras reinicias Evolution o acepta re-vincular Escritorio tras un `restart` del contenedor.

## Panel admin (landing)

- Abre en el navegador: `http://localhost:8000/admin` (mismo host/puerto que uvicorn).
- Opcional: define **`ADMIN_DASHBOARD_TOKEN`** en `.env` y entonces usa `http://localhost:8000/admin?token=TU_TOKEN` (o header `X-Admin-Token`) para ver el panel y el API.
- Verás tablas que se **actualizan solas** cada pocos segundos: perfiles por WhatsApp, oportunidades (seed + las que publiquen empleadores) y eventos de flujo.
- Para ver el **QR de Evolution** sin abrir otro panel: `http://localhost:8000/setup/evolution-qr` (mismo token que `/admin` si aplica).

## Variables de entorno

Plantilla: [env.example](env.example). El backend carga **`.env` en la raíz del repo** y, si existe, **`backend/.env`** (este último pisa valores). Los valores del archivo **pisan variables sueltas del shell** para que tu configuración sea la que manda. Si solo editabas `env.example`, copia o renombra a `.env` (el ejemplo no se carga solo).

**Seguridad (demo):** no pegues API keys reales en chats ni las subas a git. Si una clave se expuso, revócala en el panel del proveedor y genera otra solo en tu `.env` local.

Resumen:

| Variable | Uso |
|----------|-----|
| `NODE_ENV`, `PORT`, `APP_BASE_URL`, `LOG_LEVEL` | Entorno app / puerto local / URL pública base / nivel de log |
| `DATABASE_URL` | SQLite (`sqlite:///./data/app.db`) o Postgres (`postgresql://...`) |
| `REDIS_URL` | Reservado (futuro); no obligatorio para el MVP |
| `DEFAULT_LOCALE`, `DEFAULT_TIMEZONE` | Reservados (p. ej. `es`, `America/Lima`) |
| `EVOLUTION_WEBHOOK_TOKEN` o `WEBHOOK_SECRET` | Validación del webhook si Evolution envía `apikey` / Bearer |
| `EVOLUTION_MY_PHONE` | Solo dígitos con código país (ej. `51955256450` para +51 955 256 450): limita el bot a **tu** chat |
| `EVOLUTION_SELF_CHAT_MODE` | `only` (solo chat contigo), `allow` (contigo + otros), `disabled` (nunca `fromMe`) — alineado a NEKOBOT |
| `EVOLUTION_ALLOWED_JID` / `EVOLUTION_ALLOWED_JIDS` | JID completo o varios separados por coma; se normalizan; **grupos** (`@g.us`) nunca reciben respuesta |
| `EVOLUTION_API_URL` o `EVOLUTION_BASE_URL` | URL base de Evolution (alias equivalentes) |
| `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` | Envío de mensajes vía API |
| `WHATSAPP_*` | Cloud API de Meta (reservado; `WHATSAPP_SEND_MODE=off` en demo sin uso) |
| `OPENAI_*`, `ANTHROPIC_*` | LLM opcional; hoy el código usa principalmente OpenAI si hay `OPENAI_API_KEY` |
| `ADMIN_DASHBOARD_TOKEN` | Si lo defines, protege `/admin`, `/admin/api/*`, `/setup/evolution-qr`, `/setup/evolution-connect` y `/setup/evolution-status` (query `token` o header `X-Admin-Token`) |

## Evolution con Docker

En la **raíz del repo** existe `docker-compose.yml` (Evolution API + Postgres + Redis). Compose lee **`./.env`** de esa misma carpeta para sustituir `AUTHENTICATION_API_KEY` y `SERVER_URL`.

Plantillas: [env.example](env.example) y [.env.evolution.example](.env.evolution.example).

Checklist rápido tras **`docker compose up -d`**:

1. Estado: `docker compose ps` — `postgres` y `redis` en `healthy`; `evolution-api` escuchando.
2. Variables: `AUTHENTICATION_API_KEY` (en `.env` raíz para compose) debe ser **idéntico** a `EVOLUTION_API_KEY` del backend; `SERVER_URL` en local suele ser `http://localhost:8080` (ajusta si usas otro puerto/túnel).
3. Instancia: el nombre debe coincidir con `EVOLUTION_INSTANCE` (Swagger en `http://localhost:8080` o crear vía Manager).
4. QR: backend en marcha → [http://localhost:8000/setup/evolution-qr](http://localhost:8000/setup/evolution-qr).
5. Webhook: mensajes entrantes contra `POST .../webhooks/evolution`; en **`docker-compose.yml`** podés descomentar `WEBHOOK_GLOBAL_*` para apuntar a `http://host.docker.internal:8000/webhooks/evolution` solo en desarrollo. En internet hace falta HTTPS (ngrok, Cloudflare Tunnel, etc.).

```powershell
cd <raíz-del-repo>
docker compose up -d
docker compose ps
# opcional: .\scripts\validate-stack.ps1
```

En Linux con Docker motor clásico, `extra_hosts: host-gateway` permite que Evolution resuelva el backend en el host; en Docker Desktop para Windows/Mac `host.docker.internal` ya suele estar disponible.

### Conexión inestable (en el celu ves “última conexión” y el dispositivo se deshabilita)

Eso lo maneja **WhatsApp Web + Baileys dentro de Evolution**, no el backend RutaPe. Revisá en este orden:

1. **Un solo servidor Evolution** con ese número. Si tenés otro Docker/proyecto (p. ej. NEKOBOT) con el mismo WhatsApp, se pisan las sesiones y una desconecta a la otra.
2. **Pocos dispositivos vinculados.** En el teléfono: *Ajustes → Dispositivos vinculados* y borrá sesiones viejas (Web, otros Evolution, PCs que no uses).
3. **Versión de WhatsApp Web** que usa Evolution. En `.env` de la raíz podés fijar `CONFIG_SESSION_PHONE_VERSION` (valor actual por defecto en `docker-compose.yml`). Si sigue fallando, actualizá el número desde [versiones WhatsApp Web (wppconnect)](https://wppconnect.io/whatsapp-versions/), guardá `.env` y recreá API: `docker compose up -d --force-recreate evolution-api`.
4. **Logs del contenedor:** `docker logs bcp_evolution_api --tail 120` — buscá `Stream Errored`, `401`, `device_removed`, `515`, timeouts. Eso orienta si es versión, red o sesión inválida.
5. **Redis/Postgres estables.** Si el contenedor de Evolution o Redis se reinicia seguido, la sesión se cae. `docker compose ps` y revisá reinicios en Docker Desktop.
6. **Imagen más nueva (opcional).** `atendai/evolution-api:v2.1.1` es fija en el compose; en foros suelen recomendar subir de versión si hay bugs de Baileys (probalo solo si lo anterior no alcanza).

## Seguridad (MVP)

- Lista blanca de JIDs obligatoria.
- No pedir DNI ni datos sensibles en la demo.

## Si cambias el modelo de datos

En SQLite el arranque intenta `ALTER TABLE` mínimos (columnas nuevas). Si algo queda inconsistente, borra `backend/data/app.db` y reinicia para recrear tablas y seed.

## Prueba rápida sin WhatsApp

Desde la carpeta `backend`, con el entorno virtual activado:

```powershell
.\.venv\Scripts\python -c "from fastapi.testclient import TestClient; from app.main import app; 
with TestClient(app) as c: print(c.post('/company/search', json={'prompt':'python datos remoto'}).json())"
```

Usa `with TestClient(app)` para que se ejecute el arranque (tablas + seed).

## Licencia

Proyecto hackathon — ajusta licencia según tu equipo.
