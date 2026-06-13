# AI Code Review — GitHub Actions + Claude

Revisión automatizada de PRs usando **Claude** y comentarios inline vía **GitHub CLI**.

## Cómo funciona

1. Cuando abres o actualizas un PR, el action se dispara
2. Obtiene el diff del PR
3. Lo envía a **Claude** (Anthropic API) para revisión estructurada
4. Claude devuelve feedback con: `archivo`, `línea`, `severidad`, `comentario`
5. Se publica como **review en el PR** con comentarios inline en cada línea

## Setup

### 1. Agregar secrets al repo

| Secret | Valor |
|--------|-------|
| `ANTHROPIC_API_KEY` | Tu API key de Anthropic |
| `GITHUB_TOKEN` | ya viene pre-configurado (default) |

### 2. Configurar (opcional) en el workflow

- Cambiar `model` por defecto (`claude-sonnet-4-20250514`)
- Ajustar el `prompt` de review

## Resultado

Cada comentario aparece como inline en la línea exacta del archivo en el PR:

```
📌 src/app.ts | L42 | ⚠️ warning
   Considera extraer esta lógica a un helper.

📌 src/utils.ts | L15 | 🔴 error
   Esta función muta el argumento original - hacer inmutable.
```

## Personalización

Edita el `SYSTEM_PROMPT` en `.github/scripts/review.py` para cambiar el estilo o reglas de revisión. Por defecto revisa: seguridad, rendimiento, legibilidad, buenas prácticas y errores potenciales.

## Stack

- Python 3.11+
- `gh` CLI (viene en las runners de GitHub)
- Anthropic Claude API
