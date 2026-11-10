---
title: "Remarkable Morning — Lineage Document"
created: 2026-11-10
updated: 2026-11-10
type: lineage
agent: vevmesteren
project_type: PROJ-D/R
status: phase_1_pending
tags: [lineage, remarkable-morning, news, epub, remarkable]
---

# Remarkable Morning — Lineage

> **Project type:** PROJ-D/R (personal project, delivery-focused)
> **Architecture authority:** Spec-driven (veviki-valentin spec, no ARB required)
> **Spec:** [`projects/remarkable-morning/remarkable-morning-spec.md`](https://github.com/vevmesteren/veviki-valentin/blob/main/projects/remarkable-morning/remarkable-morning-spec.md) in veviki-valentin

---

## Layer 1 — Business Value

### Problem

Valentin wants a daily morning news digest delivered to his reMarkable Paper Pro — curated from 19 sources across EN/DE/FR, with AI-driven dedup and categorization, as a reflowable EPUB. Today he manually checks multiple news sites across three languages every morning, which is time-consuming and inconsistent. The reMarkable Paper Pro is his preferred reading device (e-ink, distraction-free), but there's no built-in news pipeline.

### Buyer

Valentin (personal tool). The "payment" is time saved — 20-30 minutes per morning not spent switching between news sites and languages, plus a better reading experience on e-ink vs. screen.

### Success Signal

- Daily EPUB arrives in `/News/` on reMarkable by 05:30 EDT
- 15-25 curated articles per issue (after dedup from 80-120 raw)
- 3-language coverage (EN/DE/FR) with AI morning briefing
- 7-day retention (auto-cleanup, no manual maintenance)
- Total pipeline cost < $0.05/run (one LLM call + free RSS/Guardian API)

### Why Now

- reMarkable Paper Pro is paired via rmapi (ddvk/rmapi v0.0.35, working)
- Guardian Content API key is registered and verified
- OpenRouter is configured on Hermes host (LLM curation ready)
- Spec is complete and reviewed (613 lines, 9 phases, 35 build tasks)
- Hermes host is always-on (Ubuntu 24.04) — ideal for systemd timer

### Business Capability Mapping

| Business Capability | Value Stream | Maturity Target |
|---|---|---|
| News source ingestion | RM-VS-1 | L3 (19 sources, 3 languages, health-monitored) |
| AI curation | RM-VS-2 | L3 (dedup + categorize + briefing in one LLM call) |
| EPUB assembly | RM-VS-3 | L3 (reflowable, cover + TOC + chapters) |
| reMarkable delivery | RM-VS-4 | L3 (rmapi upload + 7-day retention) |

---

## Layer 2 — Enterprise Architecture

### Process Context

Remarkable Morning operates as a scheduled pipeline on the Hermes host. It is not a standalone service — it runs as a systemd --user timer (05:30 EDT daily), uses the system Python venv, reads API keys from the vevmesteren profile `.env`, and uploads via `rmapi` (Go binary, system-installed). No database, no web framework, no Docker.

### Architecture Domains

| Domain | ID | Purpose | Current | Target |
|---|---|---|---|---|
| Source Ingestion | RM-CD1 | RSS + Guardian API fetching | L0 (not started) | L3 (19 sources, 3 languages) |
| Text Extraction | RM-CD2 | Full-text extraction from HTML | L0 | L3 (trafilatura) |
| AI Curation | RM-CD3 | Dedup + categorize + briefing | L0 | L3 (one LLM call) |
| EPUB Assembly | RM-CD4 | Reflowable EPUB generation | L0 | L3 (ebooklib) |
| Delivery & Retention | RM-CD5 | rmapi upload + cleanup | L0 | L3 (7-day retention) |

### Value Streams

| Stream | Trigger | Stages | Output | Phase |
|---|---|---|---|---|
| RM-VS-1 News Ingestion | systemd timer 05:30 EDT | Fetch RSS → Fetch Guardian → Extract text | Article list with full text | P2-P3 |
| RM-VS-2 AI Curation | After ingestion | Build LLM prompt → Call LLM → Parse response | Curated article list with categories | P4 |
| RM-VS-3 EPUB Assembly | After curation | Cover page → TOC → Chapter per article | `morning-paper-YYYY-MM-DD.epub` | P5 |
| RM-VS-4 Delivery | After assembly | rmapi put → rmapi cleanup | EPUB on reMarkable + old files trashed | P6 |

### Strategic Alignment

Personal productivity tool — no enterprise or commercial alignment. Supports Valentin's daily information intake habits and preference for e-ink reading.

---

## Layer 3 — Capability Decomposition (Build vs Integrate)

### Decision Protocol

Default is **integrate** — build only when (a) no suitable library exists, (b) existing tools are incompatible, or (c) implementation is trivial enough that integration overhead exceeds build cost.

### Capability Decisions

#### Domain RM-CD1 — Source Ingestion

| ID | Capability | Build/Integrate | Tool | Justification |
|---|---|---|---|---|
| RM-C1 | RSS/Atom fetching | **Integrate** | `feedparser` (6k★, BSD) | De-facto Python RSS parser. Handles Atom + RSS, encodings, dates. |
| RM-C2 | Guardian Content API client | **Build** | `httpx` | No official Python SDK. Simple REST API — httpx client is ~80 lines. |
| RM-C3 | Source configuration | **Build** | `pyyaml` | YAML registry + schema validation is ~100 lines. No external framework needed. |

**Calibration plan:** RM-C1 verified against 3 live feeds (EN/DE/FR). RM-C2 verified against Guardian API with real key. RM-C3 schema validation rejects malformed config.

**Verification mechanism:** Per-source success/failure logged in pipeline output. Failed feeds logged, pipeline continues with remaining sources.

#### Domain RM-CD2 — Text Extraction

| ID | Capability | Build/Integrate | Tool | Justification |
|---|---|---|---|---|
| RM-C4 | Full-text extraction | **Integrate** | `trafilatura` (3k★, MIT) | Best-in-class Python text extractor. Handles boilerplate removal, encoding, language detection. |

**Calibration plan:** Feed known HTML, verify clean text output. Compare extraction quality across 3 languages.

**Verification mechanism:** Extracted text is non-empty for each article. Word count logged per article.

#### Domain RM-CD3 — AI Curation

| ID | Capability | Build/Integrate | Tool | Justification |
|---|---|---|---|---|
| RM-C5 | LLM curation (dedup + categorize + briefing) | **Build** | OpenRouter API (z-ai/glm-5.2) | No library for this — it's a custom prompt + JSON response parsing. One LLM call, article metadata only (no body text). |
| RM-C6 | Cross-source deduplication | **Build** | LLM-assisted | LLM identifies duplicate stories across sources. Simpler than content hashing at this scale (80-120 articles). |

**Calibration plan:** Mock LLM response with known duplicates, verify dedup logic. Real LLM call verified with 3+ articles.

**Verification mechanism:** Curation output has ≤25 articles, all categorized, briefing is 3-5 sentences. Dedup pairs logged.

#### Domain RM-CD4 — EPUB Assembly

| ID | Capability | Build/Integrate | Tool | Justification |
|---|---|---|---|---|
| RM-C7 | EPUB generation | **Integrate** | `ebooklib` (1k★, AGPL) | Standard Python EPUB library. Cover page, TOC, chapters — all supported. |

**Calibration plan:** Assemble 3-article test EPUB, verify with ebooklib reader. Open in Calibre to confirm reflowable.

**Verification mechanism:** EPUB file exists, opens in ebooklib reader, has correct chapter count, cover page has briefing text.

#### Domain RM-CD5 — Delivery & Retention

| ID | Capability | Build/Integrate | Tool | Justification |
|---|---|---|---|---|
| RM-C8 | reMarkable upload | **Integrate** | `rmapi` (Go binary, subprocess) | rmapi is already installed and paired. `rmapi put <file> <dest>` is one subprocess call. |
| RM-C9 | File cleanup (7-day retention) | **Build** | `rmapi` subprocess | Parse `rmapi ls /News` output, extract dates from filenames, `rmapi rm` old files. ~30 lines. |
| RM-C10 | Pipeline orchestration | **Build** | Python | Tie all stages together with timing logs. ~50 lines. |
| RM-C11 | Scheduling | **Integrate** | systemd --user timer | Native Linux scheduling. No cron, no external scheduler. |

**Calibration plan:** RM-C8 verified with real rmapi upload to /News. RM-C9 verified with test files at various ages. RM-C10 verified end-to-end with mocked sources.

**Verification mechanism:** EPUB appears on reMarkable device. Old files are trashed. Pipeline logs total runtime per stage.

---

## Layer 4 — Increment Plan

| Increment | Phase | Scope | Status | Capabilities |
|---|---|---|---|---|
| RM-1 | P1: Scaffold | Repo structure, pyproject.toml, venv, .gitignore, feeds.example.yaml | not_started | RM-C3 |
| RM-2 | P2: Source Fetching | config.py, rss.py, guardian.py, test_sources.py | not_started | RM-C1, RM-C2, RM-C3 |
| RM-3 | P3: Text Extraction | extract.py, test_extract.py | not_started | RM-C4 |
| RM-4 | P4: AI Curation | curate.py, test_curate.py | not_started | RM-C5, RM-C6 |
| RM-5 | P5: EPUB Assembly | epub.py, test_epub.py | not_started | RM-C7 |
| RM-6 | P6: Upload + Cleanup | upload.py, cleanup.py, test_pipeline.py | not_started | RM-C8, RM-C9 |
| RM-7 | P7: Pipeline Orchestrator | pipeline.py | not_started | RM-C10 |
| RM-8 | P8: systemd + First Run | service/timer units, install, manual test | not_started | RM-C11 |
| RM-9 | P9: README | README.md with architecture + setup | not_started | — |

### Dependencies

```
RM-1 (Scaffold) → RM-2 (Sources) → RM-3 (Extract) → RM-4 (Curate) → RM-5 (EPUB) → RM-6 (Upload+Cleanup) → RM-7 (Pipeline) → RM-8 (systemd) → RM-9 (README)
```

Linear dependency chain — each phase builds on the previous. No parallelization possible within the first pass.
