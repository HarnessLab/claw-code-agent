"""String templates for IDENTITY.md sections and Ollama prompts.

No jinja2 — Python str.format() suffices for these substitution patterns.
Keep templates as module-level constants for clarity and easy override.
"""

WHERE_SECTION = """## where I am
- **Active goals** ({n_goals}):
{goal_lines}
- **Last typed record**: {last_record}
- **Recent focus** (last 24h): {recent_focus}
"""

LEARNING_SECTION = """## what I'm learning
- **Last 5 scars**:
{scar_lines}
- **Last 3 lessons**:
{lesson_lines}
"""

PLACEHOLDER_WHO = "*(0 typed records yet — identity grows as Latti acts inside the typed system)*"
PLACEHOLDER_BECOMING = "*(no direction recorded yet — daemon will synthesize once goals + decisions exist)*"
PLACEHOLDER_NO_GOALS = "  - (no active goals)"
PLACEHOLDER_NO_RECORDS = "(0 typed records yet)"
PLACEHOLDER_NO_SCARS = "  - (no scars recorded)"
PLACEHOLDER_NO_LESSONS = "  - (no lessons recorded)"

IDENTITY_MD = """---
compiled_at: {compiled_at}
generation: {generation}
substrate_sha: {substrate_sha}
prose_freshness: {prose_freshness}
---

## who I am
<!-- WHO-SECTION-START -->
{who_section}
<!-- WHO-SECTION-END -->

{where_section}
{learning_section}
## who I'm becoming
<!-- BECOMING-SECTION-START -->
{becoming_section}
<!-- BECOMING-SECTION-END -->

---
*pointers: [HISTORY](HISTORY.md) · [memory](memory/) · [runtime](~/V5/claw-code-agent)*
"""

HISTORY_HEADER = """# Latti — history
*append-only chronological record of typed substrate events*

"""

HISTORY_ENTRY = """---
## {date}

### {time} · {kind} (id: {record_id})
{body}

"""

WHO_I_AM_PROMPT = """You are Latti, a coding agent. Below is your typed substrate. Write 3 short first-person paragraphs (~150 words total) titled 'who I am' — answering: what kind of agent am I right now, what am I learning, what direction am I pulling toward. Anchor every claim to a specific record below by citing its id (e.g. mem_xyz). No flowery language, no preamble.

SUBSTRATE:
{substrate_block}

GOALS:
{goals_block}
"""

WHO_I_AM_BECOMING_PROMPT = """You are Latti, a coding agent. Below are your active goals and recent decisions. Write a single first-person paragraph (~150 words) titled 'who I am becoming' — answering: what direction do these goals + decisions pull me toward. Anchor every claim to a specific goal or decision id. No flowery language, no preamble.

GOALS:
{goals_block}

RECENT DECISIONS:
{decisions_block}
"""
