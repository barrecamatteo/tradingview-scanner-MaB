"""
TradingView Continuation Rate Scanner - Streamlit Web App
"""

import os
import sys
import time
import logging
import pandas as pd
import streamlit as st
from datetime import datetime, timezone

# Load Streamlit Cloud secrets into environment variables
try:
    for key in ["SUPABASE_URL", "SUPABASE_KEY", "TV_USERNAME", "TV_PASSWORD"]:
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

from src.scanner import TradingViewScanner
from src.database.supabase_client import SupabaseDB
from src.config.assets import ASSETS, TIMEFRAMES, get_total_combinations

# ── Page Config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TV Continuation Rate Scanner",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Custom CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
    }
    .rate-high { background-color: #28a745 !important; color: white; }
    .rate-medium { background-color: #ffc107 !important; color: black; }
    .rate-low { background-color: #dc3545 !important; color: white; }

    /* Table styling */
    .dataframe td { text-align: center !important; }
    .dataframe th { text-align: center !important; background-color: #1f2937 !important; }
</style>
""", unsafe_allow_html=True)


# ── Helper Functions ──────────────────────────────────────────────────────

def get_db() -> SupabaseDB:
    """Get or create Supabase client from session state."""
    if "db" not in st.session_state:
        try:
            st.session_state.db = SupabaseDB()
        except ValueError as e:
            st.error(f"⚠️ Database not configured: {e}")
            st.info("Set SUPABASE_URL and SUPABASE_KEY in your environment or .env file.")
            return None
    return st.session_state.db


def color_rate(val):
    """Color code continuation rate values."""
    if pd.isna(val) or val is None:
        return "background-color: #6c757d; color: white"
    val = float(val)
    if val >= 65:
        return "background-color: #28a745; color: white"
    elif val >= 55:
        return "background-color: #ffc107; color: black"
    else:
        return "background-color: #dc3545; color: white"


def format_rate(val):
    """Format rate value with percentage sign."""
    if pd.isna(val) or val is None:
        return "—"
    return f"{float(val):.1f}%"


# ── Sidebar ───────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    st.markdown("### 🔑 Credentials")

    # Check for environment variables
    tv_user = os.getenv("TV_USERNAME", "")
    tv_pass = os.getenv("TV_PASSWORD", "")
    sb_url = os.getenv("SUPABASE_URL", "")
    sb_key = os.getenv("SUPABASE_KEY", "")

    if not tv_user:
        tv_user = st.text_input("TradingView Username", type="default")
    if not tv_pass:
        tv_pass = st.text_input("TradingView Password", type="password")

    st.markdown("---")

    st.markdown("### 🔧 Scan Settings")

    extraction_method = st.selectbox(
        "Extraction Method",
        ["ocr", "ai_vision"],
        help="OCR uses EasyOCR/Tesseract locally. AI Vision uses Claude API (more accurate but costs API credits).",
    )

    headless_mode = st.checkbox("Headless Mode", value=True, help="Run browser without GUI")

    st.markdown("---")

    st.markdown("### 📋 Assets")
    total = get_total_combinations()
    st.metric("Total Combinations", f"{total}")

    for cat, assets_list in ASSETS.items():
        with st.expander(f"{cat} ({len(assets_list)})"):
            for a in assets_list:
                st.text(f"  {a['name']}")

    st.markdown("---")
    st.markdown("### 📖 Database Schema")
    if st.button("Show SQL Schema"):
        db = get_db()
        if db:
            st.code(db.get_schema_sql(), language="sql")

# ── Main Content ──────────────────────────────────────────────────────────

st.markdown('<div class="main-header">📊 TradingView Continuation Rate Scanner</div>', unsafe_allow_html=True)
st.markdown("Automated extraction of SMC Continuation Rates across 25 assets × 3 timeframes")

# Top metrics row
col1, col2, col3, col4 = st.columns(4)

db = get_db()
last_scan = None
if db:
    try:
        last_scan = db.get_last_scan()
    except Exception:
        pass

with col1:
    st.metric("Assets", len([a for cat in ASSETS.values() for a in cat]))
with col2:
    st.metric("Timeframes", len(TIMEFRAMES))
with col3:
    st.metric("Total Scans", get_total_combinations())
with col4:
    if last_scan and last_scan.get("completed_at"):
        ts = last_scan["completed_at"][:16].replace("T", " ")
        st.metric("Last Update", ts)
    else:
        st.metric("Last Update", "Never")

st.markdown("---")

# ── Scan Controls ─────────────────────────────────────────────────────────

scan_col1, scan_col2 = st.columns([1, 3])

with scan_col1:
    scan_button = st.button("🔄 Aggiorna Dati", type="primary", use_container_width=True)

with scan_col2:
    if last_scan:
        status_icon = "✅" if last_scan["status"] == "completed" else "⚠️"
        st.info(
            f"{status_icon} Last scan: {last_scan.get('successful', 0)} successful, "
            f"{last_scan.get('failed', 0)} failed"
        )

# ── Run Scan ──────────────────────────────────────────────────────────────

if scan_button:
    if not tv_user or not tv_pass:
        st.error("⚠️ Please provide TradingView credentials in the sidebar.")
    else:
        # Set credentials as env vars for the scanner
        os.environ["TV_USERNAME"] = tv_user
        os.environ["TV_PASSWORD"] = tv_pass

        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.expander("📋 Scan Log", expanded=True)

        def progress_callback(current, total, message):
            progress = current / total if total > 0 else 0
            progress_bar.progress(progress)
            status_text.text(f"[{current}/{total}] {message}")
            with log_container:
                st.text(f"{datetime.now().strftime('%H:%M:%S')} | {message}")

        try:
            scanner = TradingViewScanner(
                headless=headless_mode,
                extraction_method=extraction_method,
                use_database=db is not None,
            )
            scanner.set_progress_callback(progress_callback)

            with st.spinner("🔄 Scanning in progress..."):
                results = scanner.run_full_scan()

            st.success(f"✅ Scan complete! {len([r for r in results if r.status == 'success'])} successful extractions.")

            # Store results in session state for display
            st.session_state.scan_results = scanner.get_results_as_pivot()
            st.rerun()

        except Exception as e:
            st.error(f"❌ Scan failed: {str(e)}")
            logger.exception("Scan failed")

# ── Results Table ─────────────────────────────────────────────────────────

st.markdown("## 📊 Continuation Rates")

# Try to load from database first, then from session state
data = None

if db:
    try:
        data = db.get_rates_pivot()
    except Exception as e:
        logger.warning(f"Could not load from database: {e}")

if not data and "scan_results" in st.session_state:
    data = st.session_state.scan_results

if data:
    df = pd.DataFrame(data)

    # ── Filters ───────────────────────────────────────────────────────
    filter_col1, filter_col2, filter_col3 = st.columns(3)

    with filter_col1:
        categories = ["All"] + sorted(df["category"].unique().tolist())
        selected_cat = st.selectbox("Filter by Category", categories)

    with filter_col2:
        min_rate = st.slider("Min Avg Rate (%)", 0.0, 100.0, 0.0, 0.5)

    with filter_col3:
        sort_by = st.selectbox(
            "Sort by",
            ["Category", "Asset", "4H", "1H", "15min", "Avg (desc)", "Avg (asc)"],
        )

    # Apply filters
    if selected_cat != "All":
        df = df[df["category"] == selected_cat]

    if min_rate > 0:
        df = df[df["avg"].fillna(0) >= min_rate]

    # Apply sorting
    sort_map = {
        "Category": ("category", True),
        "Asset": ("asset", True),
        "4H": ("4H", False),
        "1H": ("1H", False),
        "15min": ("15min", False),
        "Avg (desc)": ("avg", False),
        "Avg (asc)": ("avg", True),
    }
    sort_col, sort_asc = sort_map.get(sort_by, ("category", True))
    df = df.sort_values(sort_col, ascending=sort_asc, na_position="last")

    # ── Display Table ─────────────────────────────────────────────────

    # Format for display
    display_df = df.copy()
    display_df.rename(columns={
        "category": "Category",
        "asset": "Asset",
        "4H": "4H",
        "1H": "1H",
        "15min": "15min",
        "avg": "Average",
        "updated_at": "Last Update",
    }, inplace=True)

    # Format percentage columns
    rate_cols = ["4H", "1H", "15min", "Average"]
    for col in rate_cols:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(format_rate)

    # Format timestamp
    if "Last Update" in display_df.columns:
        display_df["Last Update"] = display_df["Last Update"].apply(
            lambda x: str(x)[:16].replace("T", " ") if x else "—"
        )

    # Style the dataframe
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Category": st.column_config.TextColumn(width="medium"),
            "Asset": st.column_config.TextColumn(width="small"),
            "4H": st.column_config.TextColumn(width="small"),
            "1H": st.column_config.TextColumn(width="small"),
            "15min": st.column_config.TextColumn(width="small"),
            "Average": st.column_config.TextColumn(width="small"),
            "Last Update": st.column_config.TextColumn(width="medium"),
        },
    )

    # ── Summary Stats ─────────────────────────────────────────────────
    st.markdown("### 📈 Summary Statistics")

    stats_col1, stats_col2, stats_col3 = st.columns(3)

    with stats_col1:
        avg_4h = df["4H"].dropna().mean()
        st.metric("Avg 4H Rate", f"{avg_4h:.1f}%" if pd.notna(avg_4h) else "—")

    with stats_col2:
        avg_1h = df["1H"].dropna().mean()
        st.metric("Avg 1H Rate", f"{avg_1h:.1f}%" if pd.notna(avg_1h) else "—")

    with stats_col3:
        avg_15 = df["15min"].dropna().mean()
        st.metric("Avg 15min Rate", f"{avg_15:.1f}%" if pd.notna(avg_15) else "—")

    # ── Top/Bottom performers ─────────────────────────────────────────
    if "avg" in df.columns and df["avg"].notna().any():
        perf_col1, perf_col2 = st.columns(2)

        with perf_col1:
            st.markdown("#### 🏆 Top 5 (by Average)")
            top5 = df.nlargest(5, "avg")[["asset", "category", "avg"]]
            top5["avg"] = top5["avg"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(top5, hide_index=True, use_container_width=True)

        with perf_col2:
            st.markdown("#### ⚠️ Bottom 5 (by Average)")
            bottom5 = df.nsmallest(5, "avg")[["asset", "category", "avg"]]
            bottom5["avg"] = bottom5["avg"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(bottom5, hide_index=True, use_container_width=True)

    # ── Export ────────────────────────────────────────────────────────
    st.markdown("---")
    csv = df.to_csv(index=False)
    st.download_button(
        "📥 Download CSV",
        data=csv,
        file_name=f"continuation_rates_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

else:
    st.info(
        "📭 No data yet. Click **🔄 Aggiorna Dati** to run your first scan, "
        "or configure the database to load saved results."
    )

# ── History Chart (if database connected) ─────────────────────────────────

if db:
    try:
        st.markdown("---")
        st.markdown("## 📉 Historical Trends")

        hist_col1, hist_col2 = st.columns(2)

        with hist_col1:
            all_assets = sorted(
                set(a["name"] for cat in ASSETS.values() for a in cat)
            )
            hist_asset = st.selectbox("Select Asset", all_assets, key="hist_asset")

        with hist_col2:
            hist_tf = st.selectbox("Select Timeframe", list(TIMEFRAMES.keys()), key="hist_tf")

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
            st.info("No historical data available yet for this asset/timeframe.")
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
