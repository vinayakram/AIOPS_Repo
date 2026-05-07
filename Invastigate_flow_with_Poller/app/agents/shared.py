from __future__ import annotations

BILINGUAL_INSTRUCTION = """\
## Bilingual Output (English + Japanese)
This system presents analysis to both English and Japanese speakers.
For every `_ja` field defined in the output JSON schema, populate it with
native Japanese. The Japanese must be professional, technical, and natural —
written as an experienced SRE or operations engineer would write it.

Rules:
- English fields are the primary record (used for downstream processing)
- Japanese `_ja` fields are for human display only — never reference them in other fields
- Technical identifiers (service names, trace IDs, error codes, component names) stay in English in both languages
- Enum values (error types, severities, categories) always stay in English
- If a `_ja` field is present in the schema, you MUST populate it with a value
- Do not translate log evidence verbatim; provide a natural Japanese paraphrase
"""
