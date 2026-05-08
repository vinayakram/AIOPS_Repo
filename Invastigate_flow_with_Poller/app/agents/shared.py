"""
Shared constants injected into every agent's LLM system prompt for bilingual output support.
Defines the BILINGUAL_INSTRUCTION string that instructs GPT-4o to populate all _ja fields
with professional technical Japanese while keeping English fields as the primary data record.
Technical identifiers and enum values remain in English in both language outputs.

バイリンガル出力サポートのためにすべてのエージェントのLLMシステムプロンプトに注入される共有定数。
GPT-4oに対してすべての_jaフィールドに専門的な技術日本語を入力するよう指示するBILINGUAL_INSTRUCTION文字列を定義する。
英語フィールドはデータの主記録として保持し、技術識別子と列挙値は両言語で英語のままとする。
このモジュールはエージェント間で一貫したバイリンガル動作を保証するための単一の参照点として機能する。
"""
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
