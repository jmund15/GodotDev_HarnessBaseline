| Shape | Mandates | Merge order |
|---|---|---|
| Code | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding` | 1. `plc-pattern-fit` + `plc-evidence-grounding`; 2. that seat + `plc-architecture-quality` |
| Meta | `plc-memory-alignment`, `plc-instruction-quality`, `plc-doctrine-consistency`, `plc-evidence-grounding` | 1. text (`plc-instruction-quality` + `plc-doctrine-consistency`); 2. grounding (`plc-evidence-grounding` + `plc-memory-alignment`) |
| Mixed | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding`, `plc-instruction-quality`, `plc-doctrine-consistency` | 1. the Code merge 1 and the Meta text seat; 2. the Code merge 2 |

Split plans get one seat map. Classify each plan's shape; use the Mixed row when their shapes differ, else that shape's row. A shape-only mandate's brief names only its shape's plans; `plc-memory-alignment` and `plc-evidence-grounding` review every plan. Every finding's `file` begins with its plan's path.

Re-rank the order when the panel-yield lines of `/orchestration_metrics` flag a merged mandate or a solo seat with no unique finding.
