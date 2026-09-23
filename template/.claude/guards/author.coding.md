# Guard overlay: author — coding layer

`tools/guard_text.py` appends each section below to `author.md`'s section of the same tier; `author.md`'s header rules bind this file too.

## detailed

- Before ANY new named configuration surface (type, `[Export]`, parameter, behavior-selecting bool/enum, helper): name the family that already owns the concern, or record "none exists". A behavior-selecting bool sitting beside a `*Strategy` sibling is a strategy slot in disguise; a literal `null` into a strategy slot is a neutered seam. [rules/design_litmus.md #1]
- Orthogonal axis → a composable Resource/config slot on the base, not a subclass rung. One knob, one axis. One home per authored value — second surfaces derive, never re-author. Every visible export is read in every context an author can reach it. [rules/design_litmus.md #2–#5]

## condensed

- The design litmus before any new configuration surface: `rules/design_litmus.md` (the file states its own item count).

## minimal

Read this file's `## condensed` section.
