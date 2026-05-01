# Modelo de datos

Persistencia MVP: **SQLite** (`backend/data/app.db` si ejecutas uvicorn desde `backend/`). Los JSON en `backend/data/` son seed inicial.

## Entidades

### `UserProfile` (WhatsApp)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `wa_id` | string | JID canónico (`…@s.whatsapp.net`) |
| `role` | string | `job_seeker`, `employer` o vacío antes de elegir |
| `region`, `education_level`, `skills`, `interests`, `availability`, `goal` | text/string | Perfil persona que busca trabajo |
| `company_name`, `hiring_summary` | text | Empleador: org. y qué perfiles busca |
| `conversation_state` | string | FSM (welcome, await_role, ask_*, emp_*, ready, company_prompt, emp_pub_*, …) |
| `notes` | text | Uso temporal (borrador `/publicar`) o notas |
| `updated_at` | datetime | Última actividad (panel admin) |

### `Opportunity`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `title`, `type`, `organization`, `region`, `requirements`, `url` | — | Oferta visible en `/oportunidades` |
| `active` | bool | Si entra en matching |
| `employer_wa_id` | string nullable | Si viene de un empleador por WhatsApp |
| `created_at` | datetime nullable | Alta (admin) |

### `SeedCandidate`

Perfiles ficticios para demo y para ranking cuando aún no hay muchos `job_seeker` reales.

### `FlowEvent`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `wa_id`, `state`, `role` | string | Trazabilidad del flujo |
| `message_snippet` | text | Recorte del mensaje o comando |
| `created_at` | datetime | Orden en panel admin |

### `Company` (MVP mínimo)

Seed demo; enlaces fuertes con `Opportunity` en versiones futuras.

## Relaciones (futuro)

- `Company` 1—N `Opportunity`
- Postulaciones usuario ↔ oportunidad

En el MVP el bot recomienda y publica; no hay postulación formal.
