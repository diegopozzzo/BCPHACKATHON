# Visión del producto

## Frase central

Centralizar oportunidades laborales, de formación y de voluntariado en Perú (y remoto), y acompañar con conversación guiada para **mejorar el perfil técnico** del joven y **filtrar** lo relevante. Para empresas y aliados, **filtrar perfiles** a partir de criterios en lenguaje natural (prompt), como un asistente de reclutamiento.

## Actores

| Actor | Necesidad | Rol del agente |
|-------|-----------|----------------|
| Joven | Búsqueda de empleo engorrosa; poca orientación técnica | Onboarding breve, perfil estructurado, matches, motivación y seguimiento |
| Empresa / aliado | Muchos CVs poco alineados; poco tiempo | Catálogo de oportunidades (MVP: datos seed) y búsqueda de talento por prompt |
| Plataforma | Unificar datos y canales | WhatsApp vía Evolution en el MVP (chat propio); extensible a multiusuario |

## Objetivos del MVP (hackathon)

- Demostrar el flujo conversacional en **un número/chat** (whitelist).
- Mostrar **recomendación de oportunidades** según perfil (reglas + opcional LLM).
- Mostrar **búsqueda tipo HR** (`POST /company/search`) sobre perfiles de ejemplo.
- No solicitar datos sensibles (DNI, documentos); avisar que es demo.

## KPIs de demo (orientativos)

- Tiempo de completar perfil guiado: &lt; 3 minutos.
- Al menos 3 oportunidades rankeadas con explicación breve.
- Una consulta empresa-respuesta con top 3 perfiles.

## Disclaimer legal y técnico

Este repositorio usa **Evolution API** para integrar WhatsApp de forma no oficial respecto a las APIs de Meta. Es adecuado para prototipos y hackathon con uso responsable y número propio. Para producción con empresas y cumplimiento, se recomienda evaluar **WhatsApp Business Platform (Cloud API)** y políticas de Meta.

## Próximos pasos (post-MVP)

- Multiusuario real, panel web para empresas, ingesta de convenios, moderación y consentimiento explícito (RGPD / ley peruana de datos personales).
