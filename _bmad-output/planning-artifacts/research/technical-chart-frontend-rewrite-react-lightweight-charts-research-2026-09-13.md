---
stepsCompleted: []
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'lightweight-charts + React + React Query rewrite of troll/ml_signals/dashboard.py''s /chart/{id} page vs. the current Plotly.js/vanilla-JS architecture'
research_goals: '(1) lightweight-charts vs Plotly.js for the candlestick+oscillator+micro-panel use case -- bundle size, native pan/zoom/scroll-back-preload support, multi-pane sync, indicator overlays, licensing, maintenance status, migration cost. (2) React+React Query vs current architecture for request dedup/caching/cancellation and avoiding hand-rolled global-state patterns (e.g. the just-fixed _liveChartXRange/_indicatorFetchGen generation-counter bugs). (3) Realistic migration boundary -- full SPA vs an embedded React island for just /chart/{id}, keeping dashboard.py serving its other pages as-is. (4) Minimal-viable JS build tooling footprint given zero existing JS tooling in this repo, fitting the two-image Docker deployment split and SEC-01 (localhost-only, SSH-tunnel-only access). (5) Honest recommendation (full rewrite / embedded island / targeted fixes to current approach) weighed against this being a single-user personal tool, not a team product.'
user_name: 'Mrqdt'
date: '2026-09-13'
web_research_enabled: true
source_verification: true
---

# Research Report: technical

**Date:** 2026-09-13
**Author:** Mrqdt
**Research Type:** technical

---

## Research Overview

[Research overview and methodology will be appended here]

---

<!-- Content will be appended sequentially through research workflow steps -->
