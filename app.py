"""
Task Force One — Risk Radar + Decision Engine
Streamlit App | NYU SPS x Asana Challenge 2026

Run:
    pip install streamlit requests anthropic pypdf
    streamlit run app.py
"""

import streamlit as st
import requests
import json
import re
import io
import os
from datetime import datetime, date, timedelta
from collections import defaultdict

# ── Must be the very first Streamlit call ─────────────────────
st.set_page_config(
    page_title="Risk Radar — Task Force One",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────
ASANA_BASE          = "https://app.asana.com/api/1.0"
MAX_BRIEF_CHARS     = 2500
MAX_HISTORY_TURNS   = 6
CHAT_MAX_TOKENS     = 800
DECISION_MAX_TOKENS = 1400
SEV_ORDER           = {"🚨 CRITICAL": 0, "🔴 HIGH": 1, "🟡 MEDIUM": 2}

# ── Demo tasks with dates relative to today ────────────────────
def make_demo_tasks():
    today = date.today()
    def d(delta): return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
    def m(delta): return (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {"name": "API Authentication Module",
         "assignee": {"name": "Vasu Solanki"}, "due_on": d(-3),
         "completed": False, "memberships": [{"section": {"name": "In Progress"}}],
         "modified_at": m(-8),
         "custom_fields": [{"name": "Task Complexity", "display_value": "High"}], "notes": "Integrate OAuth2"},
        {"name": "Create Project Charter",
         "assignee": None, "due_on": d(-1),
         "completed": False, "memberships": [{"section": {"name": "Planning"}}],
         "modified_at": m(-5), "custom_fields": [], "notes": ""},
        {"name": "Stakeholder Communication Plan",
         "assignee": {"name": "Harsh Motiramani"}, "due_on": d(4),
         "completed": False, "memberships": [{"section": {"name": "Planning"}}],
         "modified_at": m(-2), "custom_fields": [], "notes": ""},
        {"name": "Risk Assessment Matrix",
         "assignee": {"name": "Vasu Solanki"}, "due_on": d(1),
         "completed": False, "memberships": [{"section": {"name": "In Progress"}}],
         "modified_at": m(-1), "custom_fields": [], "notes": ""},
        {"name": "Sprint Review Presentation",
         "assignee": {"name": "Aishwarya Purohit"}, "due_on": d(2),
         "completed": False, "memberships": [{"section": {"name": "Review"}}],
         "modified_at": m(-3), "custom_fields": [], "notes": ""},
        {"name": "Dashboard Widget Design",
         "assignee": {"name": "Vedaant Melkari"}, "due_on": None,
         "completed": False, "memberships": [{"section": {"name": "Backlog"}}],
         "modified_at": m(-6),
         "custom_fields": [{"name": "Task Complexity", "display_value": "High"}], "notes": ""},
        {"name": "Pitch Deck Finalization",
         "assignee": {"name": "Ishita Bali"}, "due_on": d(3),
         "completed": False, "memberships": [{"section": {"name": "In Progress"}}],
         "modified_at": m(-1), "custom_fields": [], "notes": ""},
        {"name": "User Testing Script",
         "assignee": {"name": "Vasu Solanki"}, "due_on": d(5),
         "completed": False, "memberships": [{"section": {"name": "Backlog"}}],
         "modified_at": m(-4), "custom_fields": [], "notes": ""},
    ]


# ─────────────────────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "tasks":           [],
        "risk_items":      [],
        "summary":         {},
        "chat_history":    [],
        "api_history":     [],
        "decisions":       [],
        "tokens_used":     0,
        "data_loaded":     False,
        "decisions_ready": False,
        "brief_cache":     {},
        "plan":            None,
        "plan_ready":      False,
        "plan_cache":      {},
        "project_start":   None,
        "project_deadline":None,
        "total_days":      None,
        "team_members":    [],
        "extracted_brief": None,   # data extracted from uploaded doc
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ─────────────────────────────────────────────────────────────
#  HELPERS — Token optimizer
# ─────────────────────────────────────────────────────────────
def compress_text(text, max_chars=MAX_BRIEF_CHARS):
    if not text:
        return ""
    # Replace special unicode that breaks JSON serialisation
    replacements = {
        "\u2014": "-",   # em dash —
        "\u2013": "-",   # en dash –
        "\u2022": "*",   # bullet •
        "\u2018": "'",   # left single quote '
        "\u2019": "'",   # right single quote '
        "\u201c": '"',   # left double quote "
        "\u201d": '"',   # right double quote "
        "\u2026": "...", # ellipsis …
        "\u00a0": " ",   # non-breaking space
        "\u2028": " ",   # line separator
        "\u2029": " ",   # paragraph separator
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Strip any remaining non-ASCII characters that could break JSON
    text = text.encode("ascii", errors="ignore").decode("ascii")
    # Collapse whitespace and strip markdown noise
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[#*_]{2,}", "", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "...[truncated]"
    return text


def compress_risk_summary(summary, risk_items):
    if not summary:
        return "No project data loaded."
    overload_str = ""
    if summary.get("overload_warnings"):
        names = ", ".join(
            f"{w['person']}({w['task_count']}t)"
            for w in summary["overload_warnings"]
        )
        overload_str = f" | Overloaded:{names}"
    top5 = sorted(risk_items, key=lambda r: SEV_ORDER.get(r["severity"], 3))[:5]
    risk_lines = "; ".join(
        f"{r['severity']} '{r['task']}'[{r['risk_type']}]→{r['recommendation']}"
        for r in top5
    )
    return (
        f"Score:{summary['health_score']}/100 "
        f"Active:{summary['total_active']} "
        f"Overdue:{summary['overdue']} "
        f"Unassigned:{summary['unassigned']} "
        f"Stalled:{summary['stalled']}"
        f"{overload_str} | TopRisks:{risk_lines}"
    )


def trim_history(history):
    if len(history) <= MAX_HISTORY_TURNS * 2:
        return history
    if history and "DECISION ENGINE" in history[0].get("content", ""):
        return [history[0]] + history[-(MAX_HISTORY_TURNS * 2 - 1):]
    return history[-(MAX_HISTORY_TURNS * 2):]


def est_tokens(text):
    return len(str(text)) // 4


# ─────────────────────────────────────────────────────────────
#  ASANA FETCHER
# ─────────────────────────────────────────────────────────────
def fetch_asana_tasks(token, project_id):
    headers = {"Authorization": f"Bearer {token}"}
    url     = f"{ASANA_BASE}/projects/{project_id}/tasks"
    params  = {
        "opt_fields": (
            "name,assignee.name,due_on,completed,"
            "memberships.section.name,dependencies,"
            "num_subtasks,custom_fields,created_at,"
            "modified_at,notes"
        )
    }
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        raise ValueError(f"Asana API error {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("data", [])


# ─────────────────────────────────────────────────────────────
#  RISK ENGINE
# ─────────────────────────────────────────────────────────────
def analyze_risks(tasks):
    today    = date.today()
    risk_items = []
    workload   = defaultdict(list)
    total = overdue_count = unassigned_count = stalled_count = critical_count = 0

    for task in tasks:
        if task.get("completed"):
            continue
        total += 1
        name          = task.get("name", "Unnamed")
        assignee      = task.get("assignee")
        assignee_name = assignee["name"] if assignee else None
        due_on        = task.get("due_on")
        modified_at   = task.get("modified_at", "")

        section = "Unknown"
        for m in task.get("memberships", []):
            section = m.get("section", {}).get("name", "Unknown")
            break

        cf_map = {}
        for cf in task.get("custom_fields", []):
            cf_map[cf.get("name", "")] = cf.get("display_value") or cf.get("text_value") or ""

        complexity   = cf_map.get("Task Complexity", "Medium")
        days_overdue = 0

        if due_on:
            due_date     = datetime.strptime(due_on, "%Y-%m-%d").date()
            days_overdue = (today - due_date).days
            if days_overdue > 0:
                overdue_count += 1
                sev = "🚨 CRITICAL" if days_overdue > 5 else "🔴 HIGH"
                risk_items.append({
                    "task": name, "risk_type": "Overdue", "severity": sev,
                    "assignee": assignee_name or "Unassigned", "section": section,
                    "detail": f"Overdue by {days_overdue}d — due {due_on}",
                    "recommendation": "Escalate and reassign" if days_overdue > 5
                                      else "Contact assignee for status update",
                })

        if not assignee_name:
            unassigned_count += 1
            risk_items.append({
                "task": name, "risk_type": "Unassigned", "severity": "🟡 MEDIUM",
                "assignee": "None", "section": section,
                "detail": "No assignee — ownership unclear",
                "recommendation": "Assign based on skill set and available capacity",
            })

        if modified_at:
            try:
                mod_date     = datetime.fromisoformat(modified_at.replace("Z", "+00:00")).date()
                days_stalled = (today - mod_date).days
                if days_stalled >= 3 and not task.get("completed"):
                    stalled_count += 1
                    risk_items.append({
                        "task": name, "risk_type": "Stalled", "severity": "🟡 MEDIUM",
                        "assignee": assignee_name or "Unassigned", "section": section,
                        "detail": f"No activity for {days_stalled} days",
                        "recommendation": "Check for blockers — request async status update",
                    })
            except Exception:
                pass

        if complexity == "High" and not due_on:
            critical_count += 1
            risk_items.append({
                "task": name, "risk_type": "No Deadline on Complex Task",
                "severity": "🔴 HIGH",
                "assignee": assignee_name or "Unassigned", "section": section,
                "detail": "High-complexity task with no due date",
                "recommendation": "Set a due date immediately to make risk trackable",
            })

        if assignee_name:
            workload[assignee_name].append({
                "task": name, "due_on": due_on,
                "complexity": complexity, "days_overdue": days_overdue,
            })

    penalty      = overdue_count*15 + unassigned_count*10 + stalled_count*5 + critical_count*10
    health_score = max(0, 100 - penalty) if total > 0 else 100

    overload_warnings = [
        {"person": p, "task_count": len(t), "tasks": [x["task"] for x in t]}
        for p, t in workload.items() if len(t) >= 3
    ]

    summary = {
        "total_active": total, "overdue": overdue_count,
        "unassigned": unassigned_count, "stalled": stalled_count,
        "critical_no_date": critical_count, "health_score": health_score,
        "overload_warnings": overload_warnings, "workload": dict(workload),
    }
    return risk_items, health_score, summary


# ─────────────────────────────────────────────────────────────
#  DECISION ENGINE
# ─────────────────────────────────────────────────────────────
def read_uploaded_brief(file_bytes, filename):
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext in (".txt", ".md"):
            return compress_text(file_bytes.decode("utf-8", errors="ignore")), None
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                text   = " ".join((p.extract_text() or "") for p in reader.pages[:6])
                return compress_text(text), None
            except ImportError:
                return None, "Run: pip install pypdf"
        else:
            return None, f"Unsupported type '{ext}'. Use .txt, .md, or .pdf"
    except Exception as e:
        return None, str(e)


def strip_markdown_fences(text):
    """
    Remove markdown code fences that Claude sometimes wraps JSON in.
    Handles: ```json { ... }``` or ``` { ... }``` or just { ... }
    """
    # Strip leading/trailing whitespace
    text = text.strip()
    # Remove opening fence: ```json or ```JSON or just ```
    text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
    # Remove closing fence
    text = re.sub(r'\s*```\s*$', '', text)
    return text.strip()


def repair_json(raw):
    """
    Four-stage JSON repair for malformed/truncated Claude output.
    Stage 0: strip markdown code fences (```json ... ```)
    Stage 1: direct parse
    Stage 2: extract object or array with regex
    Stage 3: close open strings/brackets/braces
    """
    # Stage 0 — strip markdown fences (THE most common failure cause)
    raw = strip_markdown_fences(raw)

    # Stage 1 — clean parse
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass

    # Stage 2a — extract JSON object { ... }
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group()), None
        except json.JSONDecodeError:
            pass

    # Stage 2b — extract JSON array [ ... ]
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group()), None
        except json.JSONDecodeError:
            pass

    # Stage 3 — structural repair for truncated responses
    text = raw.strip()

    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Close any open string value (response cut off mid-string)
    if not text.rstrip().endswith(('}', ']', '"')):
        text += '"'

    # Close open objects and arrays
    open_braces   = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    if open_braces > 0:
        text += '}' * open_braces
    if open_brackets > 0:
        text += ']' * open_brackets

    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass

    return None, f"All stages failed. Raw preview: {raw[:300]}"


def run_decision_engine(brief_text, compressed_state, anthropic_key):
    cache_key = hash(brief_text[:300])
    if cache_key in st.session_state.brief_cache:
        return st.session_state.brief_cache[cache_key], 0, None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
    except ImportError:
        return [], 0, "Run: pip install anthropic"

    system = (
        "You are a project decision advisor. "
        "Output ONLY a valid JSON array of exactly 10 objects. "
        "Each object MUST have exactly these keys: "
        "decision (string), context (string, max 15 words), "
        "options (array of 2-3 short strings, max 8 words each), "
        "optimal (string, max 10 words), "
        "rationale (string, max 20 words). "
        "Keep ALL values short. No markdown. No preamble. "
        "Start your response with [ and end with ]. Raw JSON only."
    )
    user_msg = (
        f"PROJECT BRIEF:\n{brief_text}\n\n"
        f"LIVE PROJECT STATE:\n{compressed_state}\n\n"
        "Identify the 10 most critical decisions this manager must make now. "
        "Rank by delivery impact. Output ONLY the raw JSON array."
    )
    try:
        resp = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 2000,           # raised from 1400 — prevents truncation
            system     = system,
            messages   = [{"role": "user", "content": user_msg}],
        )
        raw  = resp.content[0].text.strip()
        used = est_tokens(system + user_msg) + est_tokens(raw)

        decisions, err = repair_json(raw)
        if err:
            return [], used, err

        # Ensure all 10 items have the required keys with safe defaults
        sanitized = []
        for d in (decisions or [])[:10]:
            sanitized.append({
                "decision":  str(d.get("decision",  "—")),
                "context":   str(d.get("context",   "")),
                "options":   d.get("options", []) if isinstance(d.get("options"), list) else [],
                "optimal":   str(d.get("optimal",   "—")),
                "rationale": str(d.get("rationale", "—")),
            })

        st.session_state.brief_cache[cache_key] = sanitized
        return sanitized, used, None

    except Exception as e:
        return [], 0, str(e)


# ─────────────────────────────────────────────────────────────
#  CHAT ENGINE
# ─────────────────────────────────────────────────────────────
#  CHAT ENGINE
# ─────────────────────────────────────────────────────────────

def build_hours_context() -> str:
    """
    Calculate estimated hours per team member from plan tasks.
    Assumes 8 working hours per task duration day.
    Returns a compact string for the system prompt.
    """
    plan = st.session_state.get("plan")
    if not plan:
        return ""

    tasks        = plan.get("tasks", [])
    hours_by_person = {}
    tasks_by_person = {}

    for t in tasks:
        name     = t.get("assignee", "")
        dur      = max(t.get("duration_days", 1) or 1, 1)
        hours    = dur * 8  # 8hrs per day
        if name:
            hours_by_person[name] = hours_by_person.get(name, 0) + hours
            if name not in tasks_by_person:
                tasks_by_person[name] = []
            tasks_by_person[name].append(f"{t.get('name','')}({dur}d/{hours}h)")

    if not hours_by_person:
        return ""

    lines = []
    for name, total_hrs in sorted(hours_by_person.items(), key=lambda x: -x[1]):
        task_list = ", ".join(tasks_by_person[name])
        lines.append(f"{name}: {total_hrs}h total | tasks: {task_list}")

    return "HOUR ALLOCATIONS (8h/day): " + "; ".join(lines)


def build_task_context() -> str:
    """
    Build a compact per-phase task summary for the system prompt.
    Gives Claude enough detail to answer specific task questions.
    """
    plan = st.session_state.get("plan")
    if not plan:
        return ""

    tasks  = plan.get("tasks", [])
    phases = {}
    for t in tasks:
        phase = t.get("phase", "Unknown")
        if phase not in phases:
            phases[phase] = []
        phases[phase].append(
            f"{t.get('name','')} → {t.get('assignee','')} "
            f"(Day {t.get('start_day','?')}-{(t.get('start_day') or 1)+(t.get('duration_days') or 1)-1}, "
            f"{t.get('complexity','?')} complexity)"
        )

    lines = []
    for phase, phase_tasks in phases.items():
        lines.append(f"{phase}: " + " | ".join(phase_tasks))

    return "TASK PLAN: " + "; ".join(lines)


def get_ai_response(user_msg, anthropic_key):
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
    except ImportError:
        return "Run: pip install anthropic", 0

    # ── Risk summary ──────────────────────────────────────────
    compressed = compress_risk_summary(
        st.session_state.summary, st.session_state.risk_items
    )

    # ── Full team roster with roles and skills ────────────────
    team_members = st.session_state.get("team_members", [])
    if team_members:
        team_str = "; ".join(
            f"{m['name']} — {m['role']} (skills: {m['skills']})"
            for m in team_members
        )
        team_context = f"TEAM ({len(team_members)} members): {team_str}."
    else:
        workload = st.session_state.summary.get("workload", {})
        team_context = (
            "TEAM: " + ", ".join(
                f"{n} ({len(t)} tasks)" for n, t in workload.items()
            ) + "." if workload else ""
        )

    # ── Hour allocations per member ───────────────────────────
    hours_context = build_hours_context()

    # ── Full task plan by phase ───────────────────────────────
    task_context  = build_task_context()

    # ── Project dates ─────────────────────────────────────────
    proj_start    = st.session_state.get("project_start")
    proj_deadline = st.session_state.get("project_deadline")
    total_days    = st.session_state.get("total_days")
    dates_context = (
        f"PROJECT TIMELINE: {proj_start} → {proj_deadline} ({total_days} days)."
        if proj_start and proj_deadline else ""
    )

    # ── Decision engine note ──────────────────────────────────
    dec_note = (
        "The 10 manager decisions are loaded in context — reference them when relevant."
        if st.session_state.decisions_ready else ""
    )

    system = f"""You are Risk Radar AI — an intelligent project advisor for Task Force One (NYU SPS x Asana Challenge 2026).

You have complete knowledge of this project from the data below. Answer all questions directly using this data. Never say you don't have information — calculate and reason from what is provided. Never reference or suggest using Asana — this project is managed through Risk Radar only.

{dates_context}
{team_context}
{hours_context}
{task_context}
RISK STATE: {compressed}
{dec_note}

Rules:
- Answer questions about hours, tasks, members, risks, and timelines using the data above
- For hour questions: calculate from duration_days × 8 hours/day (40hr work week)
- For capacity questions: total available = duration_days × 8h per person
- Flag risks as CRITICAL / HIGH / MEDIUM / LOW
- Give concrete recommendations, not suggestions to look elsewhere
- Match response length to question complexity — short factual questions get short answers, analysis questions get full answers"""

    st.session_state.api_history.append({"role": "user", "content": user_msg})
    st.session_state.api_history = trim_history(st.session_state.api_history)

    try:
        resp = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = CHAT_MAX_TOKENS,
            system     = system,
            messages   = st.session_state.api_history,
        )
        reply = resp.content[0].text
        used  = est_tokens(system + str(st.session_state.api_history)) + est_tokens(reply)
        st.session_state.api_history.append({"role": "assistant", "content": reply})
        st.session_state.api_history = trim_history(st.session_state.api_history)
        return reply, used
    except Exception as e:
        return f"Error: {e}", 0


# ─────────────────────────────────────────────────────────────
#  UI HELPERS  (no CSS variables — use inline hex colours)
# ─────────────────────────────────────────────────────────────
def sev_color(severity):
    if "CRITICAL" in severity: return "#ef4444"
    if "HIGH"     in severity: return "#f97316"
    if "MEDIUM"   in severity: return "#eab308"
    return "#22c55e"

def score_to_color(score):
    if score >= 75: return "#22c55e"
    if score >= 50: return "#eab308"
    return "#ef4444"

def score_to_label(score):
    if score >= 75: return "Healthy"
    if score >= 50: return "At Risk"
    return "Critical"

def render_risk_card(r):
    color = sev_color(r["severity"])
    st.markdown(
        f"""
        <div style="border-left:4px solid {color};padding:10px 14px;
                    margin-bottom:8px;border-radius:0 8px 8px 0;
                    background:#f8fafc;">
          <div style="font-weight:600;font-size:14px;color:#111;">
            {r['severity']} &nbsp;·&nbsp; {r['task']}
          </div>
          <div style="font-size:13px;color:#555;margin-top:4px;">
            {r['detail']}
            &nbsp;·&nbsp; <b>Assignee:</b> {r['assignee']}
            &nbsp;·&nbsp; <b>Section:</b> {r['section']}
          </div>
          <div style="font-size:13px;color:#166534;margin-top:4px;">
            💡 {r['recommendation']}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_decision_card(i, d):
    border = "#ef4444" if i <= 3 else "#eab308" if i <= 6 else "#22c55e"
    label  = "Urgent"   if i <= 3 else "Important" if i <= 6 else "Review"
    opts   = d.get("options", [])
    opts_html = "".join(f"<li style='margin:2px 0'>{o}</li>" for o in opts)
    st.markdown(
        f"""
        <div style="border-left:4px solid {border};padding:14px 16px;
                    margin-bottom:10px;border-radius:0 8px 8px 0;background:#f8fafc;">
          <div style="font-size:12px;font-weight:600;color:{border};
                      text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">
            #{i} — {label}
          </div>
          <div style="font-size:15px;font-weight:600;color:#111;margin-bottom:6px;">
            {d.get('decision','—')}
          </div>
          {"<div style='font-size:13px;color:#555;margin-bottom:6px;'>" + d.get('context','') + "</div>" if d.get('context') else ""}
          {"<ul style='font-size:13px;color:#555;margin:4px 0 8px 16px;'>" + opts_html + "</ul>" if opts else ""}
          <div style="font-size:13px;background:#dcfce7;color:#166534;
                      padding:6px 10px;border-radius:6px;font-weight:500;">
            ✅ Optimal: {d.get('optimal','—')}
          </div>
          <div style="font-size:12px;color:#666;margin-top:6px;font-style:italic;">
            {d.get('rationale','—')}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



# ═════════════════════════════════════════════════════════════
#  PROJECT PLANNER ENGINE
#  What Asana doesn't offer:
#  Takes raw project inputs → generates a skill-matched,
#  workload-balanced task plan + manager decision agenda
#  BEFORE the project starts, not after it breaks.
# ═════════════════════════════════════════════════════════════

# ── Skill keyword map — used for local pre-matching ──────────
SKILL_KEYWORDS = {
    "backend":    ["api", "server", "database", "endpoint", "auth", "oauth",
                   "webhook", "sql", "redis", "docker", "backend", "integration",
                   "payment", "gateway", "schema", "migration", "microservice"],
    "frontend":   ["ui", "frontend", "component", "screen", "layout", "css",
                   "react", "vue", "angular", "wireframe", "prototype", "animation",
                   "onboarding", "dashboard", "responsive", "interface"],
    "design":     ["ux", "design", "wireframe", "figma", "user research",
                   "journey", "prototype", "mockup", "visual", "branding"],
    "qa":         ["test", "qa", "quality", "regression", "bug", "defect",
                   "selenium", "cypress", "integration test", "load test",
                   "performance", "end-to-end", "e2e", "security test", "pentest"],
    "ml":         ["ml", "ai", "fraud", "model", "machine learning", "prediction",
                   "detection", "neural", "dataset", "training", "inference"],
    "devops":     ["deploy", "ci/cd", "pipeline", "infrastructure", "kubernetes",
                   "docker", "terraform", "monitoring", "helm", "eks", "gke"],
    "pm":         ["plan", "charter", "stakeholder", "roadmap", "sprint",
                   "kickoff", "milestone", "communication", "presentation",
                   "scope", "requirement", "backlog", "review"],
}


def local_skill_match(task_name: str, task_desc: str = "") -> list[str]:
    """Return a list of skill domains this task likely requires."""
    text = (task_name + " " + task_desc).lower()
    matched = []
    for domain, keywords in SKILL_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matched.append(domain)
    return matched if matched else ["pm"]  # default to PM for ambiguous tasks


def build_team_skill_map(members: list[dict]) -> dict[str, list[str]]:
    """
    Build name → [skill domains] map from user-entered team members.
    members = [{"name": "...", "role": "...", "skills": "..."}]
    """
    skill_map = {}
    for m in members:
        name   = m.get("name", "").strip()
        skills = (m.get("role", "") + " " + m.get("skills", "")).lower()
        domains = []
        for domain, keywords in SKILL_KEYWORDS.items():
            if any(kw in skills for kw in keywords):
                domains.append(domain)
        skill_map[name] = domains if domains else ["pm"]
    return skill_map


def best_assignee(required_skills: list[str], skill_map: dict,
                  workload_count: dict) -> str:
    """
    Pick the team member who:
    1. Has the most skill overlap with the task
    2. Has the lowest current task count (workload balance)
    Returns the name string.
    """
    if not skill_map:
        return "Unassigned"
    best_name  = None
    best_score = -1
    for name, member_skills in skill_map.items():
        overlap = len(set(required_skills) & set(member_skills))
        # Tie-break on lowest workload — subtract 0.1 per existing task
        score   = overlap - (workload_count.get(name, 0) * 0.1)
        if score > best_score:
            best_score = score
            best_name  = name
    return best_name or list(skill_map.keys())[0]


def extract_plan_from_document(file_bytes: bytes, filename: str,
                               anthropic_key: str) -> tuple:
    """
    STRUCTURED EXTRACTION (not RAG).

    Reads a project brief document and extracts:
      - project_name  (str)
      - scope         (str)
      - goals         (str)
      - deadline      (str, e.g. "8 weeks" or "June 3 2026")
      - members       (list of {name, role, skills})

    Returns (extracted_dict, raw_text, tokens_used, error)

    Why this is NOT RAG:
    - RAG = chunk doc → vector store → retrieve relevant chunks at query time
    - This = read full doc → one extraction call → structured JSON back
    - No vectors, no index, no retrieval step. Just "read and extract."
    """
    # ── Step 1: Read the file to plain text ─────────────────
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext in (".txt", ".md"):
            raw_text = file_bytes.decode("utf-8", errors="ignore")
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader   = PdfReader(io.BytesIO(file_bytes))
                raw_text = " ".join((p.extract_text() or "") for p in reader.pages[:10])
            except ImportError:
                return None, "", 0, "Run: pip install pypdf"
        else:
            return None, "", 0, f"Unsupported type '{ext}'. Use .txt, .md, or .pdf"
    except Exception as e:
        return None, "", 0, str(e)

    if not raw_text.strip():
        return None, "", 0, "Document appears to be empty or unreadable."

    # ── Step 2: Compress text (strip unicode, collapse whitespace) ──
    compressed = compress_text(raw_text, max_chars=3000)

    # ── Step 3: One API call to extract structured fields ───
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
    except ImportError:
        return None, compressed, 0, "Run: pip install anthropic"

    system = (
        "You are a document parser. Extract project planning information from the "
        "provided text and return ONLY a valid JSON object — no markdown, no preamble. "
        "JSON schema:\n"
        "{\n"
        "  \"project_name\": str,\n"
        "  \"scope\": str (2-4 sentences describing what is in scope),\n"
        "  \"goals\": str (2-4 sentences listing the core goals),\n"
        "  \"deadline\": str (e.g. '8 weeks', 'June 3 2026', 'Q3 2026'),\n"
        "  \"members\": [{\"name\": str, \"role\": str, \"skills\": str}]\n"
        "}\n"
        "If a field is not found in the document, use an empty string or empty array. "
        "For members, infer skills from their role title if not explicitly stated. "
        "Raw JSON only. Start your response with {"
    )

    user_msg = f"Extract project planning information from this document:\n\n{compressed}"

    try:
        resp = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 1200,
            system     = system,
            messages   = [
                {"role": "user",      "content": user_msg},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw  = "{" + resp.content[0].text.strip()
        used = est_tokens(system + user_msg) + est_tokens(raw)

        extracted, err = repair_json(raw)
        if err or not extracted:
            return None, compressed, used, f"Extraction parse error: {err}"

        # Sanitize output — ensure all keys exist
        result = {
            "project_name": str(extracted.get("project_name", "")),
            "scope":        str(extracted.get("scope",        "")),
            "goals":        str(extracted.get("goals",        "")),
            "deadline":     str(extracted.get("deadline",     "")),
            "members":      extracted.get("members", [])
                            if isinstance(extracted.get("members"), list) else [],
        }
        return result, compressed, used, None

    except Exception as e:
        return None, compressed, 0, str(e)


def run_project_planner(project_name: str, scope: str, goals: str,
                        members: list[dict], deadline_str: str,
                        anthropic_key: str) -> tuple:
    """
    TWO API calls → full project plan.

    WHY TWO CALLS:
    Haiku's hard output ceiling is 4,096 tokens. A full plan for 7 members
    (15-20 tasks + 10 decisions + schedule) exceeds that in a single response,
    causing silent truncation and JSON parse failures.

    Split:
      Call 1 → tasks + schedule   (~2,500 tokens output)
      Call 2 → decisions          (~1,200 tokens output)
    Each call stays comfortably under the ceiling.
    Both results are merged into a single plan dict.

    Returns (plan_dict, tokens_used, error)
    """
    cache_key = hash(f"{project_name}{scope[:100]}{deadline_str}{len(members)}")
    if cache_key in st.session_state.plan_cache:
        return st.session_state.plan_cache[cache_key], 0, None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
    except ImportError:
        return None, 0, "Run: pip install anthropic"

    team_str     = "; ".join(f"{m['name']}({m['role']},{m['skills']})" for m in members)
    scope_short  = compress_text(scope, 350)
    goals_short  = compress_text(goals, 250)
    total_tokens = 0

    # ════════════════════════════════════════════════════════
    #  CALL 1 — Task plan + schedule
    #  Target output: ~2,000 tokens (well under 4,096 ceiling)
    # ════════════════════════════════════════════════════════
    sys1 = (
        "You are a project scheduler. Output ONLY valid JSON — no markdown, no preamble:\n"
        "{\n"
        "  \"tasks\": [{\"name\":str,\"phase\":str,\"assignee\":str,\"start_day\":int,"
        "\"duration_days\":int,\"skills_needed\":[str],\"complexity\":\"Low|Medium|High\","
        "\"why\":str(max 8 words)}],\n"
        "  \"schedule\": {\"total_days\":int,\"phases\":[{\"name\":str,"
        "\"start_day\":int,\"end_day\":int}],\"warnings\":[str]}\n"
        "}\n"
        "Rules: match tasks to member skills, max 40% tasks per member, "
        "exactly 12-15 tasks total, short strings only, start with {"
    )
    msg1 = (
        f"PROJECT: {project_name}\n"
        f"SCOPE: {scope_short}\nGOALS: {goals_short}\n"
        f"TEAM: {team_str}\nDEADLINE: {deadline_str}\n\n"
        "Output the task plan and schedule JSON."
    )

    try:
        r1   = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 3500,
            system     = sys1,
            messages   = [
                {"role": "user",      "content": msg1},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw1          = "{" + r1.content[0].text.strip()
        total_tokens += est_tokens(sys1 + msg1) + est_tokens(raw1)

        part1, err1 = repair_json(raw1)
        if err1 or not part1:
            return None, total_tokens, f"Call 1 parse error: {err1}. Preview: {raw1[:200]}"
        if "tasks" not in part1:
            return None, total_tokens, "Call 1 missing 'tasks' key"

    except Exception as e:
        return None, total_tokens, f"Call 1 API error: {e}"

    # ════════════════════════════════════════════════════════
    #  CALL 2 — Manager decisions
    #  Target output: ~1,200 tokens
    # ════════════════════════════════════════════════════════

    # Build a compact task summary to give Call 2 context
    task_summary = "; ".join(
        f"{t.get('name','')}(Day{t.get('start_day','?')},{t.get('assignee','')})"
        for t in part1.get("tasks", [])[:12]
    )

    sys2 = (
        "You are a project decision advisor. Output ONLY a valid JSON array of exactly "
        "10 objects — no markdown, no preamble.\n"
        "Each object: {\"id\":int,\"question\":str,\"why\":str(max 10 words),"
        "\"impact\":str,\"action\":str(max 10 words),"
        "\"urgency\":\"Today|This Week|Before Phase X|Standing\","
        "\"deadline_day\":int,\"depends_on\":[int]}\n"
        "Keep all strings short. Start with ["
    )
    msg2 = (
        f"PROJECT: {project_name}\nDEADLINE: {deadline_str}\n"
        f"TEAM: {team_str}\nSCOPE: {scope_short}\n"
        f"TASK PLAN SUMMARY: {task_summary}\n\n"
        "List the 10 most critical pre-project decisions ranked by urgency."
    )

    try:
        r2   = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 1800,
            system     = sys2,
            messages   = [
                {"role": "user",      "content": msg2},
                {"role": "assistant", "content": "["},
            ],
        )
        raw2          = "[" + r2.content[0].text.strip()
        total_tokens += est_tokens(sys2 + msg2) + est_tokens(raw2)

        part2, err2 = repair_json(raw2)
        if err2 or not isinstance(part2, list):
            # Decisions are non-critical — use empty list rather than failing
            part2 = []

    except Exception:
        part2 = []

    # ── Merge both calls into one plan dict ──────────────────
    plan = {
        "tasks":     part1.get("tasks",    []),
        "schedule":  part1.get("schedule", {"total_days": 56, "phases": [], "warnings": []}),
        "decisions": part2,
    }

    # ── Post-process: local skill-match safety net ───────────
    skill_map      = build_team_skill_map(members)
    workload_count = defaultdict(int)
    team_names     = [m["name"] for m in members]

    for task in plan["tasks"]:
        required = task.get("skills_needed", []) or local_skill_match(task.get("name", ""))
        if task.get("assignee", "") not in team_names:
            task["assignee"] = best_assignee(required, skill_map, workload_count)
        workload_count[task["assignee"]] += 1

    st.session_state.plan_cache[cache_key] = plan
    return plan, total_tokens, None




# ─────────────────────────────────────────────────────────────
#  INITIAL STATE
#  Always reset task/risk data on fresh load so stale
#  session data from a previous run never bleeds through.
#  Dashboard stays empty until a plan is generated.
# ─────────────────────────────────────────────────────────────
if not st.session_state.data_loaded:
    st.session_state.tasks       = []
    st.session_state.risk_items  = []
    st.session_state.summary     = {
        "total_active": 0, "overdue": 0, "unassigned": 0,
        "stalled": 0, "critical_no_date": 0, "health_score": 100,
        "overload_warnings": [], "workload": {},
    }
    st.session_state.data_loaded = True

# ── Always wipe stale task data if no plan exists yet ────────
# Prevents old demo tasks from a previous browser session
# leaking into the dashboard after a page refresh.
if not st.session_state.plan_ready:
    st.session_state.tasks       = []
    st.session_state.risk_items  = []
    st.session_state.summary     = {
        "total_active": 0, "overdue": 0, "unassigned": 0,
        "stalled": 0, "critical_no_date": 0, "health_score": 100,
        "overload_warnings": [], "workload": {},
    }


def plan_tasks_to_risk_format(plan_tasks: list, project_start: date,
                              total_days: int = 56) -> list:
    """
    Convert Project Planner task format → Risk Engine task format.

    Uses the REAL system date and the user's actual deadline to
    calculate due dates — no hardcoded offsets.

    Due date calculation:
      Claude generates start_day and duration_days relative to a
      plan_total_days (whatever it assumed for the project length).
      We scale these proportionally to the user's actual total_days
      so every task lands correctly within the real deadline window.

      scaled_start = round((start_day / plan_total) * total_days)
      due_on       = project_start + scaled_start + duration - 1

    modified_at is always set to today so stalled detection
    never fires on the day a plan is freshly generated.
    """
    today_str   = date.today().strftime("%Y-%m-%dT00:00:00Z")
    converted   = []

    # Infer what total days Claude assumed from the largest start_day in the plan
    max_start = max((t.get("start_day", 1) or 1 for t in plan_tasks), default=1)
    plan_total = max(max_start + 1, 1)  # Claude's assumed project length

    for t in plan_tasks:
        raw_start     = max(t.get("start_day", 1) or 1, 1)
        duration_days = max(t.get("duration_days", 1) or 1, 1)

        # Scale start_day proportionally from Claude's assumed length
        # to the user's actual total_days
        if plan_total > total_days:
            scaled_start = round((raw_start / plan_total) * total_days)
            scaled_start = max(scaled_start, 1)
        else:
            # Claude's plan fits within deadline — use raw values directly
            scaled_start = raw_start

        # due_on = project_start + (scaled_start - 1) + (duration - 1)
        due_date = project_start + timedelta(
            days=(scaled_start - 1) + (duration_days - 1)
        )

        # Never push due date beyond project deadline
        deadline = project_start + timedelta(days=total_days)
        if due_date > deadline:
            due_date = deadline

        assignee_name = t.get("assignee", "")
        converted.append({
            "name":      t.get("name", "Unnamed Task"),
            "assignee":  {"name": assignee_name} if assignee_name else None,
            "due_on":    due_date.strftime("%Y-%m-%d"),
            "completed": False,
            "memberships": [{"section": {"name": t.get("phase", "Unknown")}}],
            "modified_at": today_str,   # fresh — stalled counter = 0
            "custom_fields": [
                {"name": "Task Complexity",
                 "display_value": t.get("complexity", "Medium")}
            ],
            "notes": t.get("why", ""),
        })
    return converted


# ─────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/Python.svg/48px-Python.svg.png",
             width=32)
    st.markdown("## 🎯 Risk Radar")
    st.caption("Task Force One · NYU SPS x Asana 2026")
    st.divider()

    st.markdown("**🔑 Configuration**")
    anthropic_key = st.text_input("Anthropic Key", type="password",
                                   placeholder="sk-ant-...",
                                   help="console.anthropic.com → API Keys")

    # Asana fields kept as hidden variables for fetch_asana_tasks compatibility
    asana_token   = ""
    project_id    = ""
    use_live_data = False

    st.divider()

    col_a, col_b = st.columns(2)
    with col_a:
        load_clicked = st.button("Load Data", use_container_width=True, type="primary")
    with col_b:
        refresh_clicked = st.button("Refresh", use_container_width=True)

    if load_clicked or refresh_clicked:
        with st.spinner("Loading demo data…"):
            try:
                raw_tasks = make_demo_tasks()
                ri, hs, sm = analyze_risks(raw_tasks)
                st.session_state.tasks       = raw_tasks
                st.session_state.risk_items  = ri
                st.session_state.summary     = sm
                st.session_state.chat_history = []
                st.session_state.api_history  = []
                st.success(f"Loaded {len(raw_tasks)} tasks ✅")
            except Exception as ex:
                st.error(f"Error: {ex}", icon="❌")

    st.divider()
    st.markdown("**📊 Token Usage**")
    st.metric("Tokens used (est.)", f"~{st.session_state.tokens_used:,}")
    turns = len(st.session_state.api_history) // 2
    st.caption(f"Chat history: {turns}/{MAX_HISTORY_TURNS} turns")
    st.caption(f"Decision cache: {'✅ loaded' if st.session_state.decisions_ready else '— empty'}")

    st.divider()
    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.api_history  = []
        st.rerun()


# ─────────────────────────────────────────────────────────────
#  PULL CURRENT STATE
# ─────────────────────────────────────────────────────────────
summary    = st.session_state.summary
risk_items = st.session_state.risk_items
score      = summary.get("health_score", 0)
sc         = score_to_color(score)
sl         = score_to_label(score)


# ─────────────────────────────────────────────────────────────
#  HEADER
# ─────────────────────────────────────────────────────────────
st.markdown("# 🎯 Risk Radar + Decision Engine")
st.caption("Task Force One · NYU SPS x Asana Challenge 2026")
st.divider()


# ─────────────────────────────────────────────────────────────
#  TABS
# ─────────────────────────────────────────────────────────────
tab_planner, tab1, tab3, tab4 = st.tabs(
    ["🗂️ Project Planner", "📊 Dashboard", "⚡ Decision Engine", "💬 Chat"]
)



# ════════════════════════════════════════════════════════════
#  TAB 0 — PROJECT PLANNER
#  What Asana doesn't offer: pre-project intelligence.
#  Manager inputs scope + team → gets a skill-matched,
#  workload-balanced plan + 10 prioritized decisions
#  BEFORE the project clock starts.
# ════════════════════════════════════════════════════════════
with tab_planner:

    st.markdown("### 🗂️ Project Planner")
    st.markdown(
        "**What Asana doesn't do:** Start with nothing and generate a complete, "
        "skill-matched, workload-balanced project plan — plus the 10 decisions "
        "your manager must make before work begins."
    )
    st.divider()

    # ════════════════════════════════════════════════════════
    #  OPTION A — Upload a project document
    #  Claude extracts scope, goals, team from the file.
    #  This is structured extraction, NOT RAG.
    # ════════════════════════════════════════════════════════
    st.markdown("#### Option A — Upload a project document")
    st.caption(
        "Upload any project brief, summary, or charter. "
        "Claude reads the document and extracts the project name, scope, goals, "
        "deadline, and team members automatically — then pre-fills the form below. "
        "**This is structured extraction, not RAG** — no vector store, no chunking. "
        "One read → one extract call → fields populated."
    )

    upload_col, info_col = st.columns([2, 1])
    with upload_col:
        brief_upload = st.file_uploader(
            "Upload project brief / charter",
            type=["txt", "md", "pdf"],
            key="planner_brief_upload",
            help="PDF, Markdown, or plain text. Up to 10 pages read.",
        )
    with info_col:
        st.markdown("**What gets extracted:**")
        st.markdown("- Project name & deadline\n- Scope description\n- Goals list\n- Team members + roles + skills")
        st.caption("~500 tokens · result pre-fills the form below")

    if brief_upload and anthropic_key:
        if st.button("📄 Extract from Document", type="secondary", key="extract_btn"):
            with st.spinner("Reading document and extracting project details…"):
                extracted, raw_text, used, err = extract_plan_from_document(
                    brief_upload.read(), brief_upload.name, anthropic_key
                )
            if err:
                st.error(f"Extraction error: {err}", icon="❌")
            elif extracted:
                st.session_state.extracted_brief = extracted
                st.session_state.tokens_used += used
                st.success(
                    f"Extracted successfully — ~{used} tokens used. "
                    f"Form pre-filled below. Review and adjust before generating.",
                    icon="✅"
                )
                st.rerun()

    elif brief_upload and not anthropic_key:
        st.warning("Add your Anthropic Key in the sidebar to extract from a document.", icon="🔑")

    # Show what was extracted
    eb = st.session_state.get("extracted_brief")
    if eb:
        with st.expander("📋 Extracted fields — click to review", expanded=False):
            ec1, ec2 = st.columns(2)
            with ec1:
                st.markdown(f"**Project:** {eb.get('project_name','—')}")
                st.markdown(f"**Deadline:** {eb.get('deadline','—')}")
                st.markdown(f"**Scope:** {eb.get('scope','—')}")
            with ec2:
                st.markdown(f"**Goals:** {eb.get('goals','—')}")
                members_found = eb.get("members", [])
                if members_found:
                    st.markdown(f"**Team ({len(members_found)} members):**")
                    for m in members_found:
                        st.caption(f"• {m.get('name','?')} — {m.get('role','?')} ({m.get('skills','?')})")
        if st.button("🗑️ Clear extracted data", key="clear_extract"):
            st.session_state.extracted_brief = None
            st.rerun()

    st.divider()
    st.markdown("#### Option B — Enter details manually")
    st.caption("Or edit the pre-filled values from your uploaded document above.")

    # ── Pre-fill values from extraction or use empty defaults ──
    eb          = st.session_state.get("extracted_brief") or {}
    pre_name    = eb.get("project_name", "")
    pre_scope   = eb.get("scope",        "")
    pre_goals   = eb.get("goals",        "")
    pre_members = eb.get("members",      [])

    # ── Try to parse extracted deadline as a date offset ───────
    pre_deadline = date.today() + timedelta(weeks=8)  # safe default
    dl_str = eb.get("deadline", "")
    if dl_str:
        # Try to parse "N weeks"
        wk_match = re.search(r'(\d+)\s*week', dl_str, re.I)
        if wk_match:
            pre_deadline = date.today() + timedelta(weeks=int(wk_match.group(1)))
        else:
            # Try to parse a real date
            for fmt in ("%B %d %Y", "%B %d, %Y", "%Y-%m-%d", "%d %B %Y"):
                try:
                    pre_deadline = datetime.strptime(
                        re.sub(r'[,]', '', dl_str), fmt
                    ).date()
                    break
                except ValueError:
                    pass

    # ── INPUT FORM ─────────────────────────────────────────────
    with st.form("project_planner_form"):
        fc1, fc2 = st.columns([2, 1])
        with fc1:
            proj_name = st.text_input(
                "Project Name",
                value=pre_name,
                placeholder="e.g. NovaPay — Mobile Payment App",
            )
        with fc2:
            deadline_input = st.date_input(
                "Target Deadline",
                value=pre_deadline,
                min_value=date.today() + timedelta(days=1),
            )

        scope_input = st.text_area(
            "Project Scope",
            value=pre_scope,
            placeholder="Describe what is in scope…",
            height=100,
        )
        goals_input = st.text_area(
            "Project Goals",
            value=pre_goals,
            placeholder="List the core goals…",
            height=80,
        )

        st.markdown("**Team Members & Skillsets**")
        st.caption("Extracted from document if uploaded, or enter manually.")

        # Number of members — default to extracted count if available
        default_n = max(len(pre_members), 3)
        num_members = st.number_input(
            "Number of team members",
            min_value=1, max_value=10, value=default_n,
        )

        members_input = []
        member_cols   = st.columns(3)
        for i in range(int(num_members)):
            col = member_cols[i % 3]
            # Pre-fill from extracted if available
            pm = pre_members[i] if i < len(pre_members) else {}
            with col:
                st.markdown(f"**Member {i+1}**")
                mname  = st.text_input("Name",   key=f"mname_{i}",
                                        value=pm.get("name",""),
                                        placeholder="e.g. Harsh")
                mrole  = st.text_input("Role",   key=f"mrole_{i}",
                                        value=pm.get("role",""),
                                        placeholder="e.g. Backend Lead")
                mskill = st.text_input("Skills", key=f"mskill_{i}",
                                        value=pm.get("skills",""),
                                        placeholder="e.g. API, database, payments")
                if mname:
                    members_input.append({"name": mname, "role": mrole, "skills": mskill})

        submit_plan = st.form_submit_button(
            "✨ Generate Project Plan",
            type="primary",
            use_container_width=True,
        )

    # ── VALIDATION + RUN ─────────────────────────────────────
    if submit_plan:
        errors = []
        if not proj_name.strip():
            errors.append("Project name is required.")
        if not scope_input.strip():
            errors.append("Project scope is required.")
        if not goals_input.strip():
            errors.append("Project goals are required.")
        if not members_input:
            errors.append("Add at least one team member with a name.")
        if not anthropic_key:
            errors.append("Add your Anthropic Key in the sidebar.")

        if errors:
            for e in errors:
                st.error(e, icon="❌")
        else:
            project_start    = date.today()
            project_deadline = deadline_input
            total_days       = (project_deadline - project_start).days
            deadline_str     = (
                f"{project_deadline.strftime('%B %d, %Y')} "
                f"({total_days} days from today)"
            )

            with st.spinner("Generating your project plan… (one AI call, then cached)"):
                plan, used, err = run_project_planner(
                    proj_name, scope_input, goals_input,
                    members_input, deadline_str, anthropic_key
                )
            if err:
                st.error(f"Error: {err}", icon="❌")
            elif plan:
                st.session_state.plan            = plan
                st.session_state.plan_ready      = True
                st.session_state.project_start   = project_start
                st.session_state.project_deadline= project_deadline
                st.session_state.total_days      = total_days
                st.session_state.team_members    = members_input  # full roster for chat
                st.session_state.tokens_used    += used
                if used > 0:
                    st.success(f"Plan generated — ~{used:,} tokens used", icon="✅")
                else:
                    st.success("Loaded from cache — 0 tokens", icon="✅")

                # ── Auto-populate Dashboard with real plan tasks ──────
                # Pass the actual system start date and total days so
                # every due date is calculated relative to today's real date
                # and scaled to fit within the user's deadline.
                converted = plan_tasks_to_risk_format(
                    plan.get("tasks", []),
                    project_start,
                    total_days,
                )
                ri, hs, sm = analyze_risks(converted)
                st.session_state.tasks      = converted
                st.session_state.risk_items = ri
                st.session_state.summary    = sm
                st.info(
                    f"Dashboard updated — {len(converted)} tasks · "
                    f"Project runs {project_start} → {project_deadline} "
                    f"({total_days} days) · Health: {hs}/100",
                    icon="📊"
                )

    # ── OUTPUT ────────────────────────────────────────────────
    if st.session_state.plan_ready and st.session_state.plan:
        plan     = st.session_state.plan
        tasks    = plan.get("tasks", [])
        decs     = plan.get("decisions", [])
        schedule = plan.get("schedule", {})
        today_d  = date.today()

        st.divider()

        # ── Top summary strip ─────────────────────────────────
        ps1, ps2, ps3, ps4 = st.columns(4)
        ps1.metric("Total Tasks",     len(tasks))
        ps2.metric("Total Days",      schedule.get("total_days", "—"))
        ps3.metric("Phases",          len(schedule.get("phases", [])))
        ps4.metric("Team Size",       len(set(t.get("assignee","?") for t in tasks)))
        st.markdown("")

        # ── Schedule warnings ─────────────────────────────────
        warnings = schedule.get("warnings", [])
        if warnings:
            with st.expander(f"⚠️ {len(warnings)} Schedule Warning(s)", expanded=True):
                for w in warnings:
                    st.warning(w, icon="⚠️")

        out_left, out_right = st.columns([3, 2])

        # ════════════════════════════════════════════════════
        #  LEFT — Task Plan + Timeline
        # ════════════════════════════════════════════════════
        with out_left:
            st.markdown("#### 📋 Task Plan — Skill-Matched & Balanced")
            st.caption(
                "Tasks are assigned by matching task keywords to each member's skillset. "
                "Workload is spread to prevent overallocation."
            )

            # ── Phase timeline visual ─────────────────────────
            phases = schedule.get("phases", [])
            total_days = schedule.get("total_days", 1) or 1
            if phases:
                st.markdown("**Phase Timeline**")
                phase_colors = ["#3b82f6","#8b5cf6","#f97316","#22c55e","#ec4899","#06b6d4"]
                for pi, ph in enumerate(phases):
                    pct_start = (ph.get("start_day",1)-1) / total_days * 100
                    pct_width = max(
                        ((ph.get("end_day",1) - ph.get("start_day",1) + 1) / total_days * 100),
                        4
                    )
                    c = phase_colors[pi % len(phase_colors)]
                    st.markdown(
                        f"""
                        <div style="margin-bottom:6px;">
                          <div style="font-size:12px;font-weight:600;color:{c};margin-bottom:2px;">
                            {ph['name']} &nbsp;<span style="font-weight:400;color:#888;">
                            Day {ph.get('start_day','?')}–{ph.get('end_day','?')}</span>
                          </div>
                          <div style="background:#e5e7eb;border-radius:4px;height:10px;overflow:hidden;">
                            <div style="background:{c};height:100%;
                                         margin-left:{pct_start:.1f}%;width:{pct_width:.1f}%;
                                         border-radius:4px;"></div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                st.markdown("")

            # ── Tasks grouped by phase ────────────────────────
            phases_seen = []
            for t in tasks:
                if t.get("phase") not in phases_seen:
                    phases_seen.append(t.get("phase"))

            for phase_name in phases_seen:
                phase_tasks = [t for t in tasks if t.get("phase") == phase_name]
                with st.expander(f"**{phase_name}** — {len(phase_tasks)} task(s)", expanded=True):
                    for t in phase_tasks:
                        comp = t.get("complexity","Medium")
                        comp_color = {"High":"#ef4444","Medium":"#f97316","Low":"#22c55e"}.get(comp,"#888")
                        skills_badges = "".join(
                            f"<span style='background:#eff6ff;color:#1d4ed8;font-size:10px;"
                            f"padding:1px 6px;border-radius:10px;margin:1px;'>{s}</span>"
                            for s in (t.get("skills_needed") or [])
                        )
                        st.markdown(
                            f"""
                            <div style="border:0.5px solid #e5e7eb;border-radius:8px;
                                         padding:10px 12px;margin-bottom:6px;background:#fff;">
                              <div style="display:flex;justify-content:space-between;align-items:baseline;">
                                <span style="font-weight:600;font-size:14px;color:#111;">
                                  {t.get('name','?')}
                                </span>
                                <span style="font-size:11px;font-weight:600;color:{comp_color};">
                                  {comp}
                                </span>
                              </div>
                              <div style="font-size:12px;color:#555;margin:4px 0;">
                                👤 <b>{t.get('assignee','?')}</b>
                                &nbsp;·&nbsp; Day {t.get('start_day','?')}–{t.get('start_day',0)+t.get('duration_days',1)-1}
                                &nbsp;({t.get('duration_days','?')}d)
                              </div>
                              <div style="margin:4px 0;">{skills_badges}</div>
                              <div style="font-size:12px;color:#777;font-style:italic;">
                                {t.get('why','')}
                              </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

            # ── Workload balance chart ────────────────────────
            st.markdown("#### ⚖️ Workload Distribution")
            from collections import Counter
            task_counts = Counter(t.get("assignee","?") for t in tasks)
            max_count   = max(task_counts.values()) if task_counts else 1
            for person, count in sorted(task_counts.items(), key=lambda x: -x[1]):
                pct   = count / max_count * 100
                bar_c = "#ef4444" if count >= max_count * 0.8 else "#3b82f6"
                st.markdown(
                    f"""
                    <div style="margin-bottom:10px;">
                      <div style="display:flex;justify-content:space-between;
                                  font-size:13px;font-weight:500;margin-bottom:4px;">
                        <span style="color:var(--text-color);">{person}</span>
                        <span style="color:var(--text-color);opacity:0.6;">{count} task(s)</span>
                      </div>
                      <div style="background:rgba(128,128,128,0.2);border-radius:4px;
                                  height:8px;overflow:hidden;">
                        <div style="background:{bar_c};height:100%;
                                     width:{pct:.0f}%;border-radius:4px;"></div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # ════════════════════════════════════════════════════
        #  RIGHT — 10 things the manager must ask themselves
        # ════════════════════════════════════════════════════
        with out_right:
            st.markdown("#### 🧠 10 Things the Manager Must Ask Themselves")
            st.caption(
                "These are questions that must be answered *before* work starts. "
                "Asana can't surface these — it only tracks what's already assigned."
            )

            st.markdown("")

            # ── Decision cards FIRST ──────────────────────────
            for d in decs[:10]:
                did      = d.get("id", "?")
                urgency  = d.get("urgency", "")
                urg_clr  = ("#ef4444" if urgency == "Today" else
                            "#f97316" if "Week"  in urgency else
                            "#eab308" if "Phase" in urgency else "#6b7280")
                impact   = d.get("impact", "")
                imp_clr  = ("#ef4444" if "Very High" in impact or "Critical" in impact else
                            "#f97316" if "High" in impact else "#eab308")
                depends  = d.get("depends_on", [])
                dep_str  = f"Depends on: {', '.join(f'#{x}' for x in depends)}" if depends else ""

                st.markdown(
                    f"""
                    <div style="border-left:4px solid {urg_clr};padding:12px 14px;
                                margin-bottom:12px;border-radius:0 8px 8px 0;background:#fff;
                                border:0.5px solid #e5e7eb;border-left-width:4px;">
                      <div style="display:flex;justify-content:space-between;align-items:center;
                                  margin-bottom:6px;">
                        <span style="font-size:12px;font-weight:700;color:{urg_clr};">
                          #{did} · {urgency}
                        </span>
                        <span style="font-size:11px;font-weight:600;color:{imp_clr};
                                     background:{imp_clr}18;padding:2px 6px;border-radius:4px;">
                          {impact}
                        </span>
                      </div>
                      <div style="font-size:14px;font-weight:600;color:#111;margin-bottom:5px;">
                        {d.get('question','—')}
                      </div>
                      <div style="font-size:12px;color:#555;margin-bottom:7px;">
                        {d.get('why','—')}
                      </div>
                      <div style="font-size:12px;background:#dcfce7;color:#166534;
                                  padding:5px 8px;border-radius:5px;font-weight:500;">
                        ✅ {d.get('action','—')}
                      </div>
                      {"<div style='font-size:11px;color:#888;margin-top:5px;'>🔗 " + dep_str + "</div>" if dep_str else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # ── Agenda summary BELOW the decision cards ───────
            st.divider()
            st.markdown("**📅 Decision timeline summary**")
            st.caption("When each question needs to be answered")

            today_decs  = [d for d in decs if d.get("urgency","") == "Today"]
            week_decs   = [d for d in decs if "Week" in d.get("urgency","")]
            phase_decs  = [d for d in decs if "Phase" in d.get("urgency","")]
            standing    = [d for d in decs if d.get("urgency","") == "Standing"]

            def agenda_block(label, color, items):
                if not items:
                    return
                ids = ", ".join(f"#{d['id']}" for d in items)
                names_short = " · ".join(
                    d.get("question","")[:40] + ("…" if len(d.get("question","")) > 40 else "")
                    for d in items
                )
                st.markdown(
                    f"""
                    <div style="border-left:4px solid {color};padding:8px 12px;
                                margin-bottom:8px;border-radius:0 6px 6px 0;background:#f8fafc;">
                      <div style="font-size:11px;font-weight:700;color:{color};
                                  text-transform:uppercase;letter-spacing:.05em;">{label}</div>
                      <div style="font-size:12px;font-weight:600;color:#111;margin-top:2px;">{ids}</div>
                      <div style="font-size:11px;color:#666;margin-top:2px;">{names_short}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            agenda_block(f"TODAY ({today_d.strftime('%b %d')})",        "#ef4444", today_decs)
            agenda_block("THIS WEEK — Conditional on #1 outcomes",       "#f97316", week_decs)
            agenda_block("BEFORE PHASE START — QA & integration prep",   "#eab308", phase_decs)
            agenda_block("STANDING — Structural/recurring decisions",     "#6b7280", standing)



# ════════════════════════════════════════════════════════════
#  TAB 1 — DASHBOARD
# ════════════════════════════════════════════════════════════
#  TAB 1 — DASHBOARD
# ════════════════════════════════════════════════════════════
with tab1:

    if not st.session_state.plan_ready:
        st.markdown(
            """
            <div style="border:2px dashed #d1d5db;border-radius:12px;
                        padding:48px;text-align:center;margin:2rem 0;">
              <div style="font-size:36px;margin-bottom:12px;">🗂️</div>
              <div style="font-size:16px;font-weight:500;color:#374151;margin-bottom:8px;">
                No project loaded yet
              </div>
              <div style="font-size:14px;color:#9ca3af;">
                Go to the <b>Project Planner</b> tab, upload your document or enter
                project details, and click <b>Generate Project Plan</b>.
                The dashboard will automatically show your real tasks and risk scores.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        sc2 = score_to_color(summary.get("health_score", 100))
        sl2 = score_to_label(summary.get("health_score", 100))
        sc2_score = summary.get("health_score", 100)

        st.markdown(
            f"""
            <div style="background:{sc2}18;border:1px solid {sc2}44;
                        border-radius:12px;padding:20px 24px;margin-bottom:20px;
                        display:flex;align-items:center;gap:24px;">
              <div style="font-size:48px;font-weight:700;color:{sc2};line-height:1;">
                {sc2_score}
              </div>
              <div>
                <div style="font-size:20px;font-weight:600;color:{sc2};">{sl2}</div>
                <div style="font-size:13px;color:#555;">Project Health Score / 100</div>
                <div style="background:#e5e7eb;border-radius:4px;height:8px;
                            width:200px;margin-top:8px;overflow:hidden;">
                  <div style="background:{sc2};height:100%;width:{sc2_score}%;
                               border-radius:4px;"></div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Active Tasks",  summary.get("total_active", 0))
        c2.metric("Overdue",       summary.get("overdue", 0),
                  delta=f"-{summary.get('overdue',0)*15}pts" if summary.get("overdue") else None,
                  delta_color="inverse")
        c3.metric("Unassigned",    summary.get("unassigned", 0),
                  delta=f"-{summary.get('unassigned',0)*10}pts" if summary.get("unassigned") else None,
                  delta_color="inverse")
        c4.metric("Stalled",       summary.get("stalled", 0),
                  delta=f"-{summary.get('stalled',0)*5}pts" if summary.get("stalled") else None,
                  delta_color="inverse")
        c5.metric("No Deadline",   summary.get("critical_no_date", 0),
                  delta=f"-{summary.get('critical_no_date',0)*10}pts" if summary.get("critical_no_date") else None,
                  delta_color="inverse")

        st.markdown("")
        left, right = st.columns([3, 2])

        with left:
            st.markdown("### Top Risks")
            if not risk_items:
                st.success("No risks detected — project is healthy!", icon="✅")
            else:
                top = sorted(risk_items, key=lambda r: SEV_ORDER.get(r["severity"], 3))[:6]
                for r in top:
                    render_risk_card(r)
                if len(risk_items) > 6:
                    st.caption(f"+ {len(risk_items)-6} more — see the Risk Items tab")

        with right:
            st.markdown("### Team Workload")
            workload = summary.get("workload", {})
            if workload:
                for person, person_tasks in sorted(workload.items(), key=lambda x: -len(x[1])):
                    overdue_n = sum(1 for t in person_tasks if t.get("days_overdue", 0) > 0)
                    bar_pct   = min(len(person_tasks) / 6, 1.0) * 100
                    bar_c     = "#ef4444" if len(person_tasks) >= 4 else "#f97316" if len(person_tasks) >= 3 else "#22c55e"
                    overdue_badge = (
                        f"<span style=\'background:#fee2e2;color:#dc2626;font-size:11px;"
                        f"padding:2px 6px;border-radius:4px;margin-left:6px;\'>"
                        f"{overdue_n} overdue</span>"
                    ) if overdue_n else ""
                    st.markdown(
                        f"""
                        <div style="margin-bottom:12px;">
                          <div style="font-size:13px;font-weight:600;color:#111;margin-bottom:4px;">
                            {person} {overdue_badge}
                            <span style="float:right;font-size:12px;color:#888;">
                              {len(person_tasks)} task{'s' if len(person_tasks)!=1 else ''}
                            </span>
                          </div>
                          <div style="background:#e5e7eb;border-radius:4px;height:6px;overflow:hidden;">
                            <div style="background:{bar_c};height:100%;width:{bar_pct:.0f}%;
                                         border-radius:4px;"></div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            else:
                st.info("No workload data available.")

            overloads = summary.get("overload_warnings", [])
            if overloads:
                st.markdown("### ⚠️ Overload Warnings")
                for w in overloads:
                    with st.expander(f"{w['person']} — {w['task_count']} tasks"):
                        for t in w["tasks"]:
                            st.markdown(f"- {t}")


# ════════════════════════════════════════════════════════════
#  TAB 3 — DECISION ENGINE
# ════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### ⚡ Decision Engine")
    st.markdown(
        "Upload a project brief and the engine identifies the **10 most critical decisions** "
        "your manager needs to make right now — with the optimal recommendation for each. "
        "One API call, then cached for free on every follow-up."
    )
    st.divider()

    dc1, dc2 = st.columns([2, 1])

    with dc1:
        uploaded_file = st.file_uploader(
            "Upload project brief (.txt / .md / .pdf)",
            type=["txt", "md", "pdf"],
            help="PDF: first 6 pages read. Brief is compressed before sending to AI.",
        )

    with dc2:
        st.markdown("**Token budget**")
        st.markdown("""
        - First analysis: ~1,200 tokens
        - Same brief again: **0 tokens** (cached)
        - Each deep-dive: **0 tokens** (reads cache)
        """)

    if uploaded_file:
        file_bytes = uploaded_file.read()
        brief_text, err = read_uploaded_brief(file_bytes, uploaded_file.name)

        if err:
            st.error(f"Could not read file: {err}", icon="❌")
        else:
            cache_key    = hash(brief_text[:300])
            is_cached    = cache_key in st.session_state.brief_cache

            st.caption(f"Brief: {len(brief_text):,} chars after compression"
                       + (" · ✅ Cached (0 tokens)" if is_cached else ""))

            if not anthropic_key:
                st.warning("Add your Anthropic Key in the sidebar to run the Decision Engine.", icon="🔑")
            else:
                btn_label = "Reload Analysis" if is_cached else "Run Decision Engine"
                run_pressed = st.button(btn_label, type="primary")

                if run_pressed or (is_cached and not st.session_state.decisions_ready):
                    with st.spinner("Analysing brief…" if not is_cached else "Loading from cache…"):
                        compressed = compress_risk_summary(summary, risk_items)
                        decisions, used, error = run_decision_engine(
                            brief_text, compressed, anthropic_key
                        )

                    if error:
                        st.error(f"Error: {error}", icon="❌")
                    else:
                        if used > 0:
                            st.session_state.tokens_used += used
                            st.success(f"Analysis complete — {used:,} tokens used", icon="✅")
                        else:
                            st.success("Loaded from cache — 0 tokens used", icon="✅")

                        st.session_state.decisions       = decisions
                        st.session_state.decisions_ready = True

                        # Inject summary into chat context (one-time)
                        if not st.session_state.api_history:
                            dec_summary = " | ".join(
                                f"#{i+1}:{d.get('decision','?')}→{d.get('optimal','?')}"
                                for i, d in enumerate(decisions[:10])
                            )
                            st.session_state.api_history = [
                                {"role": "user",      "content": f"DECISION ENGINE LOADED: {dec_summary}"},
                                {"role": "assistant", "content": "Decision Engine loaded. I have the 10 decisions in context."},
                            ]

    # ── Show decisions ────────────────────────────────────────
    if st.session_state.decisions:
        st.divider()
        st.markdown(f"#### Top {len(st.session_state.decisions)} Decisions")
        st.caption("Red = urgent (act this week) · Yellow = important · Green = plan ahead")

        for i, d in enumerate(st.session_state.decisions[:10], 1):
            render_decision_card(i, d)

    elif not uploaded_file:
        st.markdown(
            """
            <div style="border:2px dashed #d1d5db;border-radius:12px;
                         padding:40px;text-align:center;color:#9ca3af;">
              <div style="font-size:32px;margin-bottom:8px;">📄</div>
              <div style="font-size:15px;font-weight:500;">Upload a project brief to get started</div>
              <div style="font-size:13px;margin-top:4px;">
                Supports .txt, .md, .pdf — any project documentation works
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ════════════════════════════════════════════════════════════
#  TAB 4 — CHAT
# ════════════════════════════════════════════════════════════
with tab4:
    st.markdown("### 💬 Chat with Risk Radar AI")

    if not anthropic_key:
        st.warning(
            "Add your **Anthropic Key** in the sidebar to enable AI chat. "
            "The Dashboard and Risk Items tabs work without it.",
            icon="🔑",
        )
    else:
        st.caption(
            f"Live Asana context is injected automatically · "
            f"~400 tokens/message · history trimmed to {MAX_HISTORY_TURNS} turns"
        )

        # ── Suggested starters (shown only when chat is empty) ──
        if not st.session_state.chat_history:
            st.markdown("**Start with a question:**")
            starters = [
                "What are the top 3 risks I should act on today?",
                "Who on the team is most overloaded right now?",
                "What will fail this week if I do nothing?",
                "Give me a one-paragraph project status summary.",
            ]
            sc1, sc2 = st.columns(2)
            for i, prompt in enumerate(starters):
                col = sc1 if i % 2 == 0 else sc2
                if col.button(prompt, key=f"starter_{i}", use_container_width=True):
                    st.session_state.chat_history.append(
                        {"role": "user", "content": prompt}
                    )
                    with st.spinner("Thinking…"):
                        reply, used = get_ai_response(prompt, anthropic_key)
                    st.session_state.tokens_used += used
                    st.session_state.chat_history.append(
                        {"role": "assistant", "content": reply, "tokens": used}
                    )
                    st.rerun()

            st.divider()

        # ── Chat history ────────────────────────────────────────
        for msg in st.session_state.chat_history:
            avatar = "🤖" if msg["role"] == "assistant" else "👤"
            with st.chat_message(msg["role"], avatar=avatar):
                st.markdown(msg["content"])
                if msg.get("tokens"):
                    st.caption(f"~{msg['tokens']} tokens")

        # ── Input ───────────────────────────────────────────────
        if prompt := st.chat_input("Ask about your project risks, team, decisions…"):
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
            st.session_state.chat_history.append({"role": "user", "content": prompt})

            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("Analysing…"):
                    reply, used = get_ai_response(prompt, anthropic_key)
                st.markdown(reply)
                st.caption(
                    f"~{used} tokens · "
                    f"~{st.session_state.tokens_used + used:,} total this session"
                )

            st.session_state.tokens_used += used
            st.session_state.chat_history.append(
                {"role": "assistant", "content": reply, "tokens": used}
            )