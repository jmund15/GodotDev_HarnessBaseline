---
name: No unilateral condensation when porting thorough output to a file
description: When the user asks for a thorough/comprehensive analysis and I produce one in chat, the chat content is the file spec — port it 1:1 (or fuller with native formatting), never summarize down without explicit instruction.
type: feedback
originSessionId: 874f45e2-e05b-4be4-be6d-14c69de9fc3c
---
When the user requests a "thorough," "comprehensive," "full," or otherwise depth-emphasizing output in chat and then asks me to save it to a file (Obsidian, Markdown, etc.), the chat content **is** the deliverable spec. The file copy should be **at least as detailed** — typically more detailed, since file format allows native callouts, longer code blocks, and reference appendices.

**Anti-pattern observed (2026-04-26):** I produced a comprehensive ~12k-word comparative review of two AI proposals in chat, then when asked to save to Obsidian, I unilaterally condensed it to ~8.5k tokens (≈70% density). The user pushed back: *"why did you 'condense' it?"* The condensation stripped prose elaborations, sub-bullet detail, and full paragraph reasoning under section headers — leaving headings + tables + minimal connective tissue. That's a digest, not a port.

**Why:** unilateral condensation is the same shape of mistake as unilateral deferral (cf. `feedback_recommended_fix_means_implement.md`). Both replace the user's stated intent with my own scope judgment. When the user has explicitly asked for comprehensiveness, my role is to deliver it, not to second-guess the size budget.

**How to apply:**
- When porting chat → file: copy the full prose, then enrich with file-native formatting (Obsidian callouts replace `★ Insight` blocks; Mermaid replaces ASCII diagrams; wikilinks replace plain refs). The token count should grow, not shrink.
- The only legitimate trims when porting:
  - Chat-only artifacts that don't translate (e.g., explanatory-style `★ Insight` blocks belong only in chat per the style guidelines themselves).
  - Conversational scaffolding ("Let me explain...") that doesn't add information.
- If I genuinely think the deliverable is too long and a shorter version would serve the user better, **ask first** — don't decide silently.
- Litmus test before saving: *"If the user opened this file expecting to find what I just told them in chat, would they think anything is missing?"* If yes → port more, condense less.
