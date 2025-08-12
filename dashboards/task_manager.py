# dashboards/task_manager.py
import os
import json
from datetime import datetime, date
from typing import List, Dict, Any
import streamlit as st

# ==============================
# Config y rutas
# ==============================
st.set_page_config(page_title="🗂️ Task Manager CiplasBot Wilson Calderon", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.normpath(os.path.join(BASE_DIR, "../config"))
TASKS_FILE = os.path.join(CONFIG_DIR, "tasks.json")

# ==============================
# Estilos (tema oscuro)
# ==============================
st.markdown("""
<style>
    html, body, [data-testid="stApp"] { background-color: #0e0f14; color: #eaeef3; }
    div[data-testid="stSidebar"] { background-color: #12131a; }
    .task-card {
        background: #171923;
        border: 1px solid #24283b;
        border-radius: 16px;
        padding: 14px 16px;
        margin-bottom: 10px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.35);
    }
    .pill {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 12px;
        margin-right: 6px;
        border: 1px solid #2d3250;
        background: #1d2133;
    }
    .pill-high { border-color:#5a1f1f; background:#2b1212; color:#ffb3b3; }
    .pill-med  { border-color:#5a4a1f; background:#2b2112; color:#ffe3a1; }
    .pill-low  { border-color:#1f5a2b; background:#122b18; color:#b6ffcf; }
    .pill-done { border-color:#1f4f5a; background:#12262b; color:#a9f1ff; }
    .pill-due  { border-color:#3b2d50; background:#1a1533; color:#d7c8ff; }
</style>
""", unsafe_allow_html=True)

# ==============================
# Compatibilidad rerun
# ==============================
def _rerun():
    try:
        st.rerun()
    except AttributeError:
        # Compatibilidad con versiones antiguas
        st.experimental_rerun()

# ==============================
# Utilidades
# ==============================
def _ensure_files():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump({"tasks": []}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            st.error(f"No se pudo crear {TASKS_FILE}: {e}")

def _load_tasks() -> List[Dict[str, Any]]:
    _ensure_files()
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        tasks = data.get("tasks", [])
    except Exception as e:
        st.error(f"No se pudo leer {TASKS_FILE}: {e}")
        tasks = []

    # normaliza campos esperados
    for t in tasks:
        t.setdefault("status", "pendiente")
        t.setdefault("comments", [])
    return tasks

def _save_tasks(tasks: List[dict]):
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"No se pudo guardar {TASKS_FILE}: {e}")

def _find_index(tasks: List[Dict[str, Any]], tid: str) -> int:
    for i, t in enumerate(tasks):
        if t.get("id") == tid:
            return i
    return -1

def _format_date(d: str | None) -> str:
    if not d:
        return "—"
    try:
        return datetime.fromisoformat(d).strftime("%Y-%m-%d")
    except Exception:
        return d

def _priority_pill(p: str) -> str:
    p_low = (p or "").lower()
    if p_low == "alta":
        return '<span class="pill pill-high">⚠️ Alta</span>'
    if p_low == "media":
        return '<span class="pill pill-med">⬆️ Media</span>'
    if p_low == "baja":
        return '<span class="pill pill-low">⬇️ Baja</span>'
    return f'<span class="pill">Prioridad: {p or "—"}</span>'

def _status_pill(s: str) -> str:
    s_low = (s or "").lower()
    if s_low == "pendiente":
        return '<span class="pill pill-due">🕒 Pendiente</span>'
    return '<span class="pill pill-done">✅ Hecha</span>'

def _overdue(due_iso: str | None) -> bool:
    if not due_iso:
        return False
    try:
        return date.fromisoformat(due_iso) < date.today()
    except Exception:
        return False

def _safe_date_value(due_iso: str | None) -> date:
    try:
        if due_iso:
            return date.fromisoformat(due_iso)
    except Exception:
        pass
    return date.today()

# ==============================
# Acciones sobre tareas
# ==============================
def action_mark_done(tid: str):
    tasks = _load_tasks()
    i = _find_index(tasks, tid)
    if i >= 0:
        tasks[i]["status"] = "hecha"
        tasks[i]["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)

def action_reopen(tid: str):
    tasks = _load_tasks()
    i = _find_index(tasks, tid)
    if i >= 0:
        tasks[i]["status"] = "pendiente"
        tasks[i]["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)

def action_delete(tid: str):
    tasks = _load_tasks()
    tasks = [t for t in tasks if t.get("id") != tid]
    _save_tasks(tasks)

def action_reschedule(tid: str, new_due: date):
    tasks = _load_tasks()
    i = _find_index(tasks, tid)
    if i >= 0:
        tasks[i]["due_date"] = new_due.isoformat()
        tasks[i]["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)

def action_add_comment(tid: str, author: str, comment: str):
    if not (comment or "").strip():
        return
    tasks = _load_tasks()
    i = _find_index(tasks, tid)
    if i >= 0:
        tasks[i].setdefault("comments", [])
        tasks[i]["comments"].append({
            "author": (author or "Usuario").strip(),
            "text": comment.strip(),
            "ts": datetime.now().isoformat()
        })
        tasks[i]["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)

# ==============================
# UI: Filtros y Vistas
# ==============================
st.title("🗂️ Task Manager — CiplasBot -Wilson Calderon")

tasks_all = _load_tasks()

# Barra de filtros
with st.sidebar:
    st.header("🔎 Filtros")
    status_filter = st.multiselect(
        "Estado",
        options=["pendiente", "hecha"],
        default=["pendiente"]
    )
    priorities = sorted({(t.get("priority") or "").capitalize() for t in tasks_all if t.get("priority")})
    priority_filter = st.multiselect(
        "Prioridad",
        options=priorities or ["Alta", "Media", "Baja"],
        default=priorities or ["Alta", "Media", "Baja"]
    )
    processes = sorted({t.get("process", "") for t in tasks_all if t.get("process")})
    process_filter = st.multiselect(
        "Proceso",
        options=processes or [],
        default=processes or []
    )
    # Rango de fechas
    dates = sorted([t.get("due_date") for t in tasks_all if t.get("due_date")])
    min_d = date.today()
    max_d = date.today()
    try:
        if dates:
            min_d = date.fromisoformat(min(dates))
            max_d = date.fromisoformat(max(dates))
    except Exception:
        pass
    date_range = st.date_input("Rango de vencimiento", value=(min_d, max_d))

    sort_by = st.selectbox(
        "Ordenar por",
        options=["Vencimiento (asc)", "Vencimiento (desc)", "Prioridad", "Proceso", "Estado"]
    )

# Aplica filtros
def _passes_filters(t: Dict[str, Any]) -> bool:
    if status_filter and (t.get("status", "pendiente").lower() not in status_filter):
        return False
    if priority_filter:
        pr = (t.get("priority") or "").capitalize()
        if pr and pr not in priority_filter:
            return False
    if process_filter:
        prc = t.get("process", "")
        if prc and prc not in process_filter:
            return False
    # fecha
    try:
        d_from, d_to = date_range if isinstance(date_range, tuple) else (date_range, date_range)
        if t.get("due_date"):
            d = date.fromisoformat(t["due_date"])
            if d < d_from or d > d_to:
                return False
    except Exception:
        pass
    return True

tasks = [t for t in tasks_all if _passes_filters(t)]

# Ordenamiento
def _priority_rank(p: str) -> int:
    m = {"alta": 0, "media": 1, "baja": 2}
    return m.get((p or "").lower(), 3)

if sort_by == "Vencimiento (asc)":
    tasks.sort(key=lambda x: (x.get("due_date") or "9999-12-31", _priority_rank(x.get("priority")), x.get("process","")))
elif sort_by == "Vencimiento (desc)":
    tasks.sort(key=lambda x: (x.get("due_date") or "0000-01-01"), reverse=True)
elif sort_by == "Prioridad":
    tasks.sort(key=lambda x: _priority_rank(x.get("priority")))
elif sort_by == "Proceso":
    tasks.sort(key=lambda x: x.get("process",""))
elif sort_by == "Estado":
    tasks.sort(key=lambda x: x.get("status","pendiente"))

# Tabs por vista
tab1, tab2, tab3 = st.tabs(["📅 Por fecha", "⏫ Por prioridad", "🏭 Por proceso"])

def render_task_card(t: Dict[str, Any]):
    tid = t.get("id")
    name = t.get("name", "—")
    due = _format_date(t.get("due_date"))
    prio = t.get("priority", "—")
    proc = t.get("process", "—")
    status = t.get("status", "pendiente")
    created_by = t.get("created_by", "—")

    overdue = _overdue(t.get("due_date"))

    st.markdown('<div class="task-card">', unsafe_allow_html=True)
    cols = st.columns([6, 3, 3, 2, 2])
    with cols[0]:
        st.markdown(f"### {name}")
        st.markdown(
            f'{_priority_pill(prio)} {_status_pill(status)} '
            f'<span class="pill">🆔 {tid}</span> '
            f'<span class="pill">👤 Creador: {created_by}</span> '
            f'<span class="pill">🏭 {proc}</span> ',
            unsafe_allow_html=True
        )
        if overdue and status != "hecha":
            st.warning("⏰ *Vencida* — reprograma la fecha.", icon="⚠️")

    with cols[1]:
        st.caption("Vencimiento")
        st.markdown(f"**{due}**")

    with cols[2]:
        st.caption("Reprogramar fecha")
        current_value = _safe_date_value(t.get('due_date'))
        new_due = st.date_input(" ", key=f"due_{tid}", value=current_value)
        if st.button("💾 Reprogramar", key=f"resched_{tid}"):
            action_reschedule(tid, new_due)
            st.success("Fecha actualizada.")
            _rerun()

    with cols[3]:
        st.caption("Estado")
        if status == "pendiente":
            if st.button("✅ Marcar hecha", key=f"done_{tid}"):
                action_mark_done(tid)
                st.success("Tarea completada.")
                _rerun()
        else:
            if st.button("↩️ Reabrir", key=f"reopen_{tid}"):
                action_reopen(tid)
                st.info("Tarea reabierta.")
                _rerun()

    with cols[4]:
        st.caption("Eliminar")
        if st.button("🗑️ Eliminar", key=f"del_{tid}"):
            action_delete(tid)
            st.warning("Tarea eliminada.")
            _rerun()

    # Comentarios
    st.divider()
    st.subheader("💬 Comentarios")
    comments = t.get("comments", [])
    if comments:
        for c in comments[::-1][:5]:
            when = c.get("ts","")[:19].replace("T"," ")
            st.markdown(f"- **{c.get('author','Usuario')}** · _{when}_: {c.get('text','')}")
    else:
        st.caption("No hay comentarios.")

    c_cols = st.columns([3, 7, 2])
    with c_cols[0]:
        author = st.text_input("Autor", key=f"c_author_{tid}", value=t.get("created_by","Usuario"))
    with c_cols[1]:
        msg = st.text_input("Escribe un comentario…", key=f"c_text_{tid}", value="")
    with c_cols[2]:
        if st.button("➕ Agregar", key=f"c_add_{tid}"):
            action_add_comment(tid, author, msg)
            st.success("Comentario agregado.")
            _rerun()

    st.markdown('</div>', unsafe_allow_html=True)

def section_by_date():
    # agrupar por fecha (string)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        k = _format_date(t.get("due_date"))
        groups.setdefault(k, []).append(t)
    for due_key in sorted(groups.keys()):
        st.markdown(f"## 📅 {due_key}")
        for t in groups[due_key]:
            render_task_card(t)

def section_by_priority():
    order = ["Alta", "Media", "Baja", "—"]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        k = (t.get("priority") or "—").capitalize()
        groups.setdefault(k, []).append(t)
    for pr in order:
        if pr in groups:
            st.markdown(f"## ⏫ {pr}")
            for t in groups[pr]:
                render_task_card(t)

def section_by_process():
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        k = t.get("process") or "—"
        groups.setdefault(k, []).append(t)
    for proc in sorted(groups.keys()):
        st.markdown(f"## 🏭 {proc}")
        for t in groups[proc]:
            render_task_card(t)

with tab1:
    section_by_date()
with tab2:
    section_by_priority()
with tab3:
    section_by_process()

# ==============================
# Agregar tarea rápida (opcional)
# ==============================
st.sidebar.divider()
st.sidebar.subheader("➕ Nueva tarea rápida")
with st.sidebar.form("quick_add"):
    q_name = st.text_input("Nombre")
    q_due = st.date_input("Vence", value=date.today())
    q_prio = st.selectbox("Prioridad", ["Alta", "Media", "Baja"])
    q_proc = st.text_input("Proceso", value="")
    q_creator = st.text_input("Creador", value="Administrador")
    submitted = st.form_submit_button("Crear tarea")
    if submitted:
        tasks_now = _load_tasks()
        new_task = {
            "id": datetime.now().strftime("T%Y%m%d%H%M%S%f"),
            "name": (q_name or "").strip() or "Tarea sin nombre",
            "due_date": q_due.isoformat(),
            "priority": q_prio,
            "process": (q_proc or "").strip() or "General",
            "status": "pendiente",
            "created_at": datetime.now().isoformat(),
            "created_by_phone": "",
            "created_by": (q_creator or "").strip() or "Administrador",
            "comments": []
        }
        tasks_now.append(new_task)
        _save_tasks(tasks_now)
        st.success("Tarea creada.")
        _rerun()
