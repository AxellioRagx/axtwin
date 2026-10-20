# ai-crafted — the agentic twin-config experiment

Output of the Stage-1/Stage-2 "scenario AI research" experiment: an agent
harvested a video corpus and synthesized twin configs from it. Kept beside the
instrument for reference; nothing in `engine/` or `discovery/` reads it.

- `corpus/` — the harvested Stage-1 corpus (videos, index, run logs).
  Regenerable, **gitignored** (`/.gitignore`).
- `aiscenario/` — the research pipeline's working tree (empty in this baseline;
  the pipeline itself was dropped — root `Claude.md`, "Frozen").

The synthesized scenarios it produced ship as `catalog/target-environments/`.
