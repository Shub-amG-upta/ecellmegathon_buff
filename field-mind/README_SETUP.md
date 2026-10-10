# FieldMind multi-agent starter kit

Unzip this in the repo root (the existing repo). It adds:

| File | What it is |
| --- | --- |
| `CLAUDE_multiagent_section.md` | Paste at the end of the repo's `CLAUDE.md`, then delete this file |
| `.claude/settings.json` | Shared Claude Code permissions: tests, benchmark and read-only git run without asking; `git push`, `adb shell rm` and `.env` are blocked |
| `.claude/skills/bench/SKILL.md` | `/bench` - run all 30 episodes and compare against the baseline |
| `.claude/skills/board-up/SKILL.md` | `/board-up` - start the NPU and CPU `llama-server` lanes on the QIDK and verify them |
| `.claude/agents/phase-reviewer.md` | A read-only reviewer that checks each finished phase against the plan's rules |
| `docs/multi_agent_plan.pdf` | The architecture plan the sessions build from |
| `PROMPTS.md` | One prompt per Claude Code session, in order |

If the repo already has a `.claude/settings.json`, merge the `permissions` lists instead of overwriting it.
