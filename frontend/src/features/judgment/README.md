# frontend/src/features/judgment

[中文说明](README_zh.md)

Experimental semantic-judgment UI copy and `search_skills` recommendation chips. Settings widgets live in `components/customize/ExperimentsTab.tsx`. Do not edit generated `i18n/en.ts` / `zh.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`copy.ts`](copy.ts) | Feature-local bilingual copy (`judgmentT`, built on onboarding's `copyLookup`). |
| [`chips.ts`](chips.ts) | Parse list vs dict `search_skills` payloads; render experimental suggestion chips. |
| [`chips.test.ts`](chips.test.ts) | List shape unchanged; dict shape paints chips, p_fit, confidence, hover version, status notes. |
| [`judgment.css`](judgment.css) | Customize block + chip styles. Uses existing CSS tokens. |
| [`index.ts`](index.ts) | Public re-exports. |
