# Multi-Source Candidate Data Transformer

**Candidate:** Akshay KS 
**Email:** `akshayks1005@gmail.com`  
**Goal:** Convert messy candidate inputs into one canonical, normalized, deduplicated, explainable candidate profile without inventing unknown values.

## Pipeline

The transformer is deterministic and runs in seven stages:

**Detect / Adapt:** Each source is handled by a `SourceAdapter`. This project supports recruiter CSV, ATS JSON, and GitHub public REST API profiles. Missing files, malformed JSON, empty inputs, API failures, and rate limits return warnings or empty extractions rather than crashing the run.

**Extract:** Structured adapters read explicit fields such as name, email, phone, company, title, skills, links, experience, and education. The GitHub adapter calls public user and repository endpoints, extracting profile fields plus repository languages as lower-confidence skill signals.

**Normalize:** Values are normalized before merge: emails are lowercased, Gmail aliases are resolved for identity, phones become E.164, dates become `YYYY-MM`, locations become `{city, region, country}` with ISO country codes, URLs are canonicalized to HTTPS, and skills are mapped through a taxonomy.

**Merge:** Records are grouped by deterministic identity: normalized email hash first, then name + phone fallback, then name-only fallback with lower confidence. List fields are unioned and deduplicated. Scalar fields are resolved by confidence and source priority.

**Confidence / Provenance:** Each value receives confidence from source reliability and extraction method. Provenance records field, source, method, raw value, normalized value, confidence, and conflict status. Missing values remain null; the system never fabricates data.

**Project:** The internal canonical record is kept separate from output projection. Runtime YAML config can select fields, rename paths, flatten nested fields, apply transforms, filter arrays, toggle provenance/confidence, and choose missing-value behavior: `null`, `omit`, or `error`.

**Validate:** The final JSON is validated before returning.

## Canonical Schema

```text
candidate_id
full_name
emails[]
phones[]                  # E.164
location {city, region, country}
links {linkedin, github, portfolio, other[]}
headline
years_experience
skills[] {name, confidence, sources[]}
experience[] {company, title, start, end, summary}
education[] {institution, degree, field, end_year}
provenance[]
overall_confidence
```

## Merge and Conflict Policy

Source priority is:

```text
ATS JSON > Recruiter CSV > GitHub API
```

For scalar fields such as name, headline, and location, the highest-confidence/highest-priority value wins. Losing conflicting values are kept in provenance with `conflict=true`; they are not silently discarded. For list fields such as emails, phones, skills, and links, the transformer takes the normalized union. GitHub repository languages are included with a confidence penalty because language usage is a proxy, not a self-declared skill.

## Runtime Configuration

Example projection behavior:

```yaml
provenance: false
confidence: true
missing_value_policy: "omit"
fields:
  - source: "full_name"
    target: "candidate_name"
    required: true
  - source: "location.country"
    target: "country_code"
  - source: "skills"
    target: "technical_skills"
    filter: "confidence > 0.65"
    transform: "pluck:name"
```

This makes one canonical record reusable for multiple downstream schemas without code changes.

## Edge Cases and Scope

Handled: empty or missing sources, invalid JSON, invalid email-only rows, duplicate candidates across CSV/ATS, phone/date/skill normalization failures, GitHub API failures, missing public GitHub email, and conflicting field values.

Left out under time pressure: full resume PDF/DOCX parsing, LinkedIn scraping, ML-based entity extraction, fuzzy identity matching beyond email/phone/name, and distributed processing infrastructure. The adapter and merge boundaries are designed so those can be added later.
