"""
TradingView Continuation Rate Scanner - Streamlit Web App
"""

import os
import sys
import logging
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timezone, timedelta, date

# Load Streamlit Cloud secrets into environment variables
try:
    for key in ["SUPABASE_URL", "SUPABASE_KEY", "GITHUB_TOKEN"]:
        if key in st.secrets and not os.getenv(key):
            os.environ[key] = st.secrets[key]
except Exception:
    pass

# Load .env file for local development
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database.supabase_client import SupabaseDB
from src.config.assets import ASSETS, TIMEFRAMES, get_total_combinations

# ── Page Config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TV Continuation Rate Scanner",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_REPO = "barrecamatteo/tradingview-scanner"
GITHUB_WORKFLOW = "scheduled_scan.yml"

# ── Custom CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .dataframe td { text-align: center !important; }
    .dataframe th { text-align: center !important; }
</style>
""", unsafe_allow_html=True)


# ── Helper Functions ──────────────────────────────────────────────────────

def get_db() -> SupabaseDB:
    if "db" not in st.session_state:
        try:
            st.session_state.db = SupabaseDB()
        except ValueError as e:
            st.error(f"⚠️ Database non configurato: {e}")
            return None
    return st.session_state.db


def format_rate(val):
    if pd.isna(val) or val is None:
        return "—"
    return f"{float(val):.1f}%"


def trigger_github_scan() -> bool:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        st.error("⚠️ GITHUB_TOKEN non configurato nei Secrets.")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.post(url, headers=headers, json={"ref": "main"})
        return response.status_code == 204
    except Exception as e:
        st.error(f"Errore: {e}")
        return False


def get_workflow_status() -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW}/runs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.get(url, headers=headers, params={"per_page": 1})
        if response.status_code == 200:
            runs = response.json().get("workflow_runs", [])
            if runs:
                run = runs[0]
                return {
                    "status": run["status"],
                    "conclusion": run.get("conclusion"),
                    "created_at": run["created_at"],
                    "id": run["id"],
                }
        return None
    except Exception:
        return None


def cancel_workflow(run_id: int) -> bool:
    """Cancel a running GitHub Actions workflow."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/cancel"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.post(url, headers=headers)
        return response.status_code == 202
    except Exception:
        return False


def get_scan_dates(db) -> list:
    """Get all dates that have scan data."""
    try:
        result = db.client.table("scan_log") \
            .select("started_at, successful, failed, status") \
            .eq("status", "completed") \
            .order("started_at", desc=True) \
            .execute()

        dates = []
        for row in result.data:
            dt = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            dates.append({
                "date": dt.date(),
                "datetime": row["started_at"],
                "successful": row.get("successful", 0),
                "failed": row.get("failed", 0),
            })
        return dates
    except Exception:
        return []


def get_history_for_date(db, target_date: date) -> list:
    """Get all scan results for a specific date from history table."""
    try:
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
        end = start + timedelta(days=1)

        result = db.client.table("continuation_rates_history") \
            .select("*") \
            .gte("scanned_at", start.isoformat()) \
            .lt("scanned_at", end.isoformat()) \
            .order("scanned_at", desc=True) \
            .execute()

        return result.data
    except Exception as e:
        logger.warning(f"Errore caricamento storico: {e}")
        return []


def pivot_history_data(history_data: list) -> list:
    """Convert flat history records into pivot format (one row per asset)."""
    asset_data = {}

    for row in history_data:
        key = row["asset"]
        if key not in asset_data:
            asset_data[key] = {
                "asset": row["asset"],
                "category": row["category"],
                "4H": None,
                "1H": None,
                "15min": None,
                "scanned_at": row["scanned_at"],
            }
        if row["cont_rate"] is not None:
            asset_data[key][row["timeframe"]] = float(row["cont_rate"])

    return sorted(asset_data.values(), key=lambda x: (x["category"], x["asset"]))


def get_top_rates(df, threshold=67.5) -> pd.DataFrame:
    """
    Extract individual asset/timeframe combinations above threshold.
    Returns a DataFrame with columns: Asset, Categoria, Timeframe, Cont. Rate
    """
    rows = []
    for _, row in df.iterrows():
        for tf in ["4H", "1H", "15min"]:
            if tf in row and pd.notna(row[tf]) and row[tf] is not None:
                val = float(row[tf])
                if val >= threshold:
                    rows.append({
                        "Asset": row["asset"],
                        "Categoria": row["category"],
                        "Timeframe": tf,
                        "Cont. Rate": f"{val:.1f}%",
                        "_sort_val": val,
                    })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    result = result.sort_values("_sort_val", ascending=False).drop(columns=["_sort_val"])
    return result.reset_index(drop=True)


# ── Sidebar ───────────────────────────────────────────────────────────────

db = get_db()

with st.sidebar:
    st.markdown("### 👤 MBARRECA")

    if db:
        st.markdown("✅ Database OK")
    else:
        st.markdown("❌ Database non connesso")

    workflow = get_workflow_status()
    if workflow:
        if workflow["status"] == "in_progress":
            st.markdown("⏳ Scansione in corso...")
        else:
            st.markdown("✅ API GitHub OK")

    st.markdown("---")

    # ── Calendar / Date Picker ────────────────────────────────────────
    st.markdown("### 📅 Storico Analisi")

    scan_dates = []
    if db:
        scan_dates = get_scan_dates(db)

    available_dates = [s["date"] for s in scan_dates]

    # ── Month navigation ──────────────────────────────────────────────
    import calendar

    if "cal_year" not in st.session_state:
        st.session_state.cal_year = date.today().year
    if "cal_month" not in st.session_state:
        st.session_state.cal_month = date.today().month

    nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
    with nav_col1:
        if st.button("◀", key="prev_month", use_container_width=True):
            if st.session_state.cal_month == 1:
                st.session_state.cal_month = 12
                st.session_state.cal_year -= 1
            else:
                st.session_state.cal_month -= 1
            st.rerun()
    with nav_col2:
        month_names_it = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu",
                          "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
        st.markdown(
            f"<div style='text-align:center; font-weight:bold; font-size:1.1em;'>"
            f"{month_names_it[st.session_state.cal_month]} {st.session_state.cal_year}</div>",
            unsafe_allow_html=True,
        )
    with nav_col3:
        if st.button("▶", key="next_month", use_container_width=True):
            if st.session_state.cal_month == 12:
                st.session_state.cal_month = 1
                st.session_state.cal_year += 1
            else:
                st.session_state.cal_month += 1
            st.rerun()

    # ── Build calendar HTML ───────────────────────────────────────────
    cal = calendar.Calendar(firstweekday=0)  # Monday first
    month_days = cal.monthdayscalendar(st.session_state.cal_year, st.session_state.cal_month)
    today = date.today()

    cal_html = """
    <style>
    .cal-table { width: 100%; border-collapse: collapse; margin: 5px 0; }
    .cal-table th { color: #888; font-size: 0.75em; font-weight: 600; padding: 4px 0; text-align: center; }
    .cal-table td { text-align: center; padding: 3px 0; font-size: 0.85em; }
    .cal-day { width: 28px; height: 28px; line-height: 28px; margin: auto; border-radius: 50%; }
    .cal-today { background-color: #4A9EE5; color: white; font-weight: bold; }
    .cal-saved { background-color: #48C78E; color: white; font-weight: bold; }
    .cal-empty { color: #ccc; }
    .cal-normal { color: #333; }
    .cal-legend { display: flex; align-items: center; justify-content: center; gap: 15px; margin: 8px 0; font-size: 0.75em; color: #888; }
    .cal-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 4px; }
    .cal-dot-green { background-color: #48C78E; }
    .cal-dot-blue { background-color: #4A9EE5; }
    </style>
    <table class="cal-table">
    <tr><th>Lu</th><th>Ma</th><th>Me</th><th>Gi</th><th>Ve</th><th>Sa</th><th>Do</th></tr>
    """

    for week in month_days:
        cal_html += "<tr>"
        for day in week:
            if day == 0:
                cal_html += '<td><div class="cal-day cal-empty"></div></td>'
            else:
                d = date(st.session_state.cal_year, st.session_state.cal_month, day)
                if d == today and d in available_dates:
                    cls = "cal-saved"
                elif d == today:
                    cls = "cal-today"
                elif d in available_dates:
                    cls = "cal-saved"
                else:
                    cls = "cal-normal"
                cal_html += f'<td><div class="cal-day {cls}">{day}</div></td>'
        cal_html += "</tr>"

    cal_html += "</table>"
    cal_html += """
    <div class="cal-legend">
        <span><span class="cal-dot cal-dot-green"></span>Analisi salvata</span>
        <span><span class="cal-dot cal-dot-blue"></span>Oggi</span>
    </div>
    """

    st.markdown(cal_html, unsafe_allow_html=True)

    # ── Dropdown: Carica analisi ──────────────────────────────────────
    st.markdown("")
    st.markdown("📋 **Carica analisi:**")

    date_options = ["-- Seleziona data --"]
    date_map = {}
    for s in scan_dates:
        label = s["date"].strftime("%d/%m/%Y") + f" ({s['successful']} asset)"
        date_options.append(label)
        date_map[label] = s["date"]

    selected_option = st.selectbox(
        "Carica analisi",
        date_options,
        key="date_selector",
        label_visibility="collapsed",
    )

    if selected_option != "-- Seleziona data --":
        selected_date = date_map[selected_option]
    else:
        selected_date = date.today()

    # ── Vai a Oggi button ─────────────────────────────────────────────
    st.markdown("")
    if st.button("📍 Vai a Oggi", use_container_width=True):
        st.session_state.date_selector = "-- Seleziona data --"
        st.session_state.cal_year = date.today().year
        st.session_state.cal_month = date.today().month
        st.rerun()

    st.markdown("---")

    st.markdown("### 📋 Asset Monitorati")
    for cat, assets_list in ASSETS.items():
        with st.expander(f"{cat} ({len(assets_list)})"):
            for a in assets_list:
                st.text(f"  {a['name']}")


# ── Main Content ──────────────────────────────────────────────────────────

st.markdown('<div class="main-header">📊 TradingView Continuation Rate Scanner</div>', unsafe_allow_html=True)
st.markdown("Scansione automatica dei Continuation Rate SMC su 25 asset × 3 timeframe")

# Top metrics row
col1, col2, col3, col4 = st.columns(4)

last_scan = None
if db:
    try:
        last_scan = db.get_last_scan()
    except Exception:
        pass

with col1:
    st.metric("Asset", len([a for cat in ASSETS.values() for a in cat]))
with col2:
    st.metric("Timeframe", len(TIMEFRAMES))
with col3:
    st.metric("Combinazioni", get_total_combinations())
with col4:
    if last_scan and last_scan.get("completed_at"):
        ts = last_scan["completed_at"][:16].replace("T", " ")
        st.metric("Ultimo Aggiornamento", ts)
    else:
        st.metric("Ultimo Aggiornamento", "Mai")

st.markdown("---")

# ── Scan Button ───────────────────────────────────────────────────────────

scan_col1, scan_col2, scan_col3 = st.columns([1, 1, 2])

is_running = bool(workflow and workflow.get("status") in ("in_progress", "queued"))

with scan_col1:
    if st.button("🚀 Avvia Scansione", type="primary", use_container_width=True, disabled=is_running):
        if trigger_github_scan():
            st.success(
                "✅ Scansione avviata su GitHub Actions! "
                "I risultati appariranno qui tra circa 45-60 minuti."
            )
            st.rerun()
        else:
            st.error("❌ Impossibile avviare la scansione.")

with scan_col2:
    if is_running:
        if st.button("⏹ Ferma Scansione", type="secondary", use_container_width=True):
            if cancel_workflow(workflow["id"]):
                st.success("✅ Scansione fermata!")
                st.rerun()
            else:
                st.error("❌ Impossibile fermare la scansione.")

with scan_col3:
    if last_scan:
        status_icon = "✅" if last_scan["status"] == "completed" else "⏳"
        st.info(
            f"{status_icon} Ultima scansione: {last_scan.get('successful', 0)} riuscite, "
            f"{last_scan.get('failed', 0)} fallite"
        )
    if is_running:
        st.warning("⏳ Scansione GitHub in corso... Ricarica la pagina tra qualche minuto.")

# ── Load Data Based on Selected Date ──────────────────────────────────────

st.markdown("## 📊 Continuation Rates")

data = None
showing_date = None

if db:
    if selected_date == date.today():
        try:
            data = db.get_rates_pivot()
            showing_date = "più recenti"
        except Exception as e:
            logger.warning(f"Errore: {e}")
    else:
        history = get_history_for_date(db, selected_date)
        if history:
            data = pivot_history_data(history)
            showing_date = selected_date.strftime("%d/%m/%Y")
        else:
            st.warning(f"⚠️ Nessun dato trovato per il {selected_date.strftime('%d/%m/%Y')}")

if showing_date:
    st.caption(f"📅 Dati: **{showing_date}**")

if data:
    df = pd.DataFrame(data)

    # Remove avg column if present (from db pivot)
    if "avg" in df.columns:
        df = df.drop(columns=["avg"])

    # ── Filters ───────────────────────────────────────────────────────
    filter_col1, filter_col2 = st.columns(2)

    with filter_col1:
        categories = ["Tutti"] + sorted(df["category"].unique().tolist())
        selected_cat = st.selectbox("Filtra per Categoria", categories)

    with filter_col2:
        sort_by = st.selectbox(
            "Ordina per",
            ["Categoria", "Asset", "4H", "1H", "15min"],
        )

    # Apply filters
    if selected_cat != "Tutti":
        df = df[df["category"] == selected_cat]

    # Apply sorting
    sort_map = {
        "Categoria": ("category", True),
        "Asset": ("asset", True),
        "4H": ("4H", False),
        "1H": ("1H", False),
        "15min": ("15min", False),
    }
    sort_col, sort_asc = sort_map.get(sort_by, ("category", True))
    df = df.sort_values(sort_col, ascending=sort_asc, na_position="last")

    # ── Display Table ─────────────────────────────────────────────────

    display_df = df.copy()

    col_rename = {"category": "Categoria", "asset": "Asset"}
    if "updated_at" in display_df.columns:
        col_rename["updated_at"] = "Ultimo Agg."
    if "scanned_at" in display_df.columns:
        col_rename["scanned_at"] = "Ultimo Agg."

    display_df.rename(columns=col_rename, inplace=True)

    for col in ["4H", "1H", "15min"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(format_rate)

    if "Ultimo Agg." in display_df.columns:
        display_df["Ultimo Agg."] = display_df["Ultimo Agg."].apply(
            lambda x: str(x)[:16].replace("T", " ") if x else "—"
        )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Categoria": st.column_config.TextColumn(width="medium"),
            "Asset": st.column_config.TextColumn(width="small"),
            "4H": st.column_config.TextColumn(width="small"),
            "1H": st.column_config.TextColumn(width="small"),
            "15min": st.column_config.TextColumn(width="small"),
            "Ultimo Agg.": st.column_config.TextColumn(width="medium"),
        },
    )

    # ── Top Performers (>67.5%) ───────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🏆 Top Continuation Rates (≥ 67.5%)")

    top_rates = get_top_rates(df, threshold=67.5)

    if not top_rates.empty:
        st.dataframe(
            top_rates,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Asset": st.column_config.TextColumn(width="small"),
                "Categoria": st.column_config.TextColumn(width="medium"),
                "Timeframe": st.column_config.TextColumn(width="small"),
                "Cont. Rate": st.column_config.TextColumn(width="small"),
            },
        )
        st.caption(f"Trovati **{len(top_rates)}** combinazioni con Cont. Rate ≥ 67.5%")
    else:
        st.info("Nessuna combinazione asset/timeframe supera il 67.5%")

    # ── Export ────────────────────────────────────────────────────────
    st.markdown("---")
    csv = df.to_csv(index=False)
    st.download_button(
        "📥 Scarica CSV",
        data=csv,
        file_name=f"continuation_rates_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

else:
    if not showing_date or showing_date == "più recenti":
        st.info(
            "📭 Nessun dato disponibile. Clicca **🚀 Avvia Scansione** per lanciare "
            "la prima raccolta dati."
        )

# ── History Chart ─────────────────────────────────────────────────────────

if db:
    try:
        st.markdown("---")
        st.markdown("## 📉 Storico Variazioni")

        hist_col1, hist_col2 = st.columns(2)

        with hist_col1:
            all_assets = sorted(
                set(a["name"] for cat in ASSETS.values() for a in cat)
            )
            hist_asset = st.selectbox("Seleziona Asset", all_assets, key="hist_asset")

        with hist_col2:
            hist_tf = st.selectbox("Seleziona Timeframe", list(TIMEFRAMES.keys()), key="hist_tf")

        history = db.get_history(asset=hist_asset, timeframe=hist_tf, limit=50)

        if history:
            hist_df = pd.DataFrame(history)
            hist_df["scanned_at"] = pd.to_datetime(hist_df["scanned_at"])
            hist_df = hist_df.sort_values("scanned_at")

            st.line_chart(
                hist_df.set_index("scanned_at")["cont_rate"],
                use_container_width=True,
            )
        else:
            st.info("Nessun dato storico disponibile per questo asset/timeframe.")
    except Exception:
        pass

# ── Footer ────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: #6c757d; font-size: 0.8rem;'>"
    "TradingView Continuation Rate Scanner | SMC Market Structure Analysis"
    "</div>",
    unsafe_allow_html=True,
)
