# Bilingual QA Checklist

This document exercises the English → Chinese path with structural elements.

## Checklist

- [x] Cover page renders
- [x] Table of contents has page numbers
- [ ] Header and footer appear on every page

## Table

| Check | Expected |
| --- | --- |
| Table header repeats | yes |
| Long code wraps | yes |

## Code

```js
const api = await fetch("/api/health");
console.log(await api.json());
```

> Note: structural validation must reject translations that drop blocks.

Footnote reference[^qa].

[^qa]: Structure-preserving translation is verified automatically.
