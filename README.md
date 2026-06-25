# Risk Radar — AI Project Risk & Decision Engine

> Pre-project intelligence for project managers. Generate a skill-matched, workload-balanced plan, score live project risk, and surface the decisions a manager must make **before** the project breaks — not after.

Built for the **NYU SPS × Asana Challenge 2026** (Team: Task Force One). A single-file Streamlit app powered by Anthropic's Claude.

---

## What it does

Most project tools tell you what already went wrong. Risk Radar focuses on the part that happens **before** the work starts and **between** status updates — turning a raw brief and a team roster into an actionable plan, a continuously scored risk picture, and a ranked decision agenda.

Four tabs, four jobs:

**🗂️ Project Planner** — Feed it a scope, goals, team, and deadline (or upload a brief and let it extract those fields), and it returns a 12–15 task plan with phases, skill-matched assignees, a schedule, and the 10 most critical pre-project decisions. Tasks are matched to team skills and balanced so no one carries more than ~40% of the load.

**📊 Dashboard** — A 0–100 project health score with the risk drivers behind it: overdue, unassigned, stalled, and high-complexity-without-a-deadline tasks, plus per-person workload and overload warnings.

**⚡ Decision Engine** — Upload a project brief and get the 10 highest-impact decisions a manager needs to make right now, each with options, an optimal recommendation, and the rationale. Cached after the first run so follow-ups cost zero tokens.

**💬 Chat** — Ask about risks, capacity, hours, timelines, or any specific task. Live project state is injected automatically, so answers are grounded in the actual plan instead of generic advice.

---

## Engineering highlights

This started as a hackathon entry but the interesting parts are in how it stays cheap, fast, and reliable on top of an LLM:

- **Local risk engine** — Health scoring and risk classification (overdue / unassigned / stalled / no-deadline) run in pure Python, so the dashboard works with zero API calls.
- **Skill-matching + load balancing** — A keyword-based skill map (backend, frontend, design, QA, ML, DevOps, PM) assigns each task to the best-fit, least-loaded team member, with an LLM pass and a local safety net.
- **Token-aware architecture** — Brief compression, response caching by content hash, and history trimming keep usage low; a live token meter shows the cost of every action.
- **Two-call planning** — Plan generation is split into *tasks + schedule* and *decisions* to stay under the model's output ceiling and avoid silent truncation.
- **4-stage JSON repair** — Strips markdown fences, extracts objects/arrays, and closes truncated brackets so malformed model output rarely breaks the UI.
- **Structured extraction (not RAG)** — Documents are read once and parsed into a structured schema (no vector store, no chunking, no retrieval step).

---

## Tech stack

`Python` · `Streamlit` · `Anthropic Claude (Haiku 4.5)` · `pypdf` · `Asana REST API`

---

## Getting started

```bash
# 1. Clone
git clone https://github.com/<your-username>/risk-radar.git
cd risk-radar

# 2. Install
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

Then add your Anthropic API key in the sidebar (get one at [console.anthropic.com](https://console.anthropic.com)). Click **Load Data** to explore with demo tasks, or open the **Project Planner** tab to generate a plan from scratch.

> The AI-powered tabs (Planner, Decision Engine, Chat) require an API key. The Dashboard and risk scoring run locally without one.

---

## Project structure

```
risk-radar/
├── app.py            # Full Streamlit app: risk engine, planner, decision engine, chat
├── requirements.txt
├── LICENSE
└── README.md
```

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Harsh Motiramani](https://harshmotiramani.com) · [LinkedIn](https://www.linkedin.com/in/harsh-motiramani-608321201/)
