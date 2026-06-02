# Contract Review Skills — Summary

This document consolidates all skills directly related to **contract review** from:

- `ai-legal-claude-main`
- `claude-legal-skill-main`

---

## A. Skills from `ai-legal-claude-main`

### 1) `legal-review` (flagship review)
- **Source**: `ai-legal-claude-main/skills/legal-review/SKILL.md`
- **Command**: `/legal review <file>`
- **Purpose**: Flagship contract review workflow — 5 parallel subagents + aggregated scoring
- **Key outputs**:
  - Contract Safety Score (0–100)
  - Risk tiers (high / medium / low)
  - Clause-by-clause analysis and fix recommendations
  - Missing protections
  - Obligations and deadline timeline
  - Compliance flags and negotiation priorities

### 2) `legal-risks` (deep risk review)
- **Source**: `ai-legal-claude-main/skills/legal-risks/SKILL.md`
- **Command**: `/legal risks <file>`
- **Purpose**: Clause-by-clause risk scoring (1–10) + financial exposure estimates
- **Key outputs**:
  - Overall Risk Score
  - Risk matrix (clause / category / score / exposure)
  - Hidden risks (definition traps, cross-reference traps, survival clauses, etc.)
  - Top 5 remediation priorities

### 3) `legal-compare` (contract comparison review)
- **Source**: `ai-legal-claude-main/skills/legal-compare/SKILL.md`
- **Command**: `/legal compare <file1> <file2>`
- **Purpose**: Side-by-side clause comparison; who benefits and how risk shifts
- **Key outputs**:
  - Change summary (added / removed / substantive / cosmetic)
  - Favorability shift (which party gains)
  - Dangerous changes (sneaked clauses, stripped protections, etc.)
  - Priority pushback recommendations

### 4) `legal-plain` (review aid: plain English)
- **Source**: `ai-legal-claude-main/skills/legal-plain/SKILL.md`
- **Command**: `/legal plain <file>`
- **Purpose**: Translate legalese into plain English to support review understanding
- **Key outputs**:
  - Quick reference summary
  - Per-clause “original vs. plain English”
  - Flags (WATCH OUT, HIDDEN OBLIGATION, etc.)
  - Glossary of legal terms

### 5) `legal-negotiate` (post-review negotiation)
- **Source**: `ai-legal-claude-main/skills/legal-negotiate/SKILL.md`
- **Command**: `/legal negotiate <file>`
- **Purpose**: Generate send-ready counter-proposals for unfavorable clauses
- **Key outputs**:
  - MUST / SHOULD / NICE priority tiers
  - Replacement clause text per issue (ready to insert)
  - Negotiation talking points and acceptance likelihood
  - Ready-to-send email template

### 6) `legal-missing` (missing protections review)
- **Source**: `ai-legal-claude-main/skills/legal-missing/SKILL.md`
- **Command**: `/legal missing <file>`
- **Purpose**: Find protections that should be in the contract but are absent
- **Key outputs**:
  - Gaps classified as CRITICAL / IMPORTANT / RECOMMENDED
  - Risk explanation and real-world scenario per gap
  - Suggested full clause language to add
  - Protection Coverage Score

### 7) `legal-freelancer` (freelancer-perspective review)
- **Source**: `ai-legal-claude-main/skills/legal-freelancer/SKILL.md`
- **Command**: `/legal freelancer <file>`
- **Purpose**: Review freelancer/contractor agreements for common traps
- **Key outputs**:
  - Freelancer Fairness Score (0–100)
  - 20-item Freelancer Bill of Rights checklist
  - Common trap detection (unlimited revisions, no kill fee, overbroad IP assignment, etc.)
  - Negotiation scripts

### 8) Main router (review-related)
- **Source**: `ai-legal-claude-main/legal/SKILL.md`
- **Purpose**: `/legal` command entry point and routing (including `/legal review`, `/legal risks`, etc.)
- **Role**: Dispatches user input to the appropriate review skill

---

## B. Skill from `claude-legal-skill-main`

### 9) `contract-review`
- **Source**: `claude-legal-skill-main/skill.md`
- **Version**: `3.0.0`
- **Purpose**: General-purpose contract review (NDA, SaaS, employment, M&A, etc.)
- **Capabilities**:
  - CUAD-based risk categories
  - Position-aware (adjusts flags by whether you are buyer, seller, customer, vendor, etc.)
  - Market benchmarks (industry baseline comparison)
  - Negotiability ratings
  - Direct redline suggestions + missing-provision language
  - Internal consistency checks (cross-references, undefined terms)

---

## C. Recommended workflow

1. Run `legal-review` or `contract-review` for a full first pass  
2. Run `legal-risks` + `legal-missing` to lock in high-risk items and missing protections  
3. Run `legal-negotiate` for outbound, send-ready revision language  
4. Use `legal-compare` when comparing two versions or two contracts

---

## D. Notes

- All entries are extracted and summarized from the original `SKILL.md` / `skill.md` files.  
- This file is a **readable index** for quick lookup, skill selection, and workflow design.  
- If you need a **full copy of each original skill file**, a `raw/` subdirectory can be added under this folder with the original path structure preserved.
