"""
Corporate Web Portal -- Energy Trading Briefing Agent (PS130)

Danske Gas Corporate Website Aesthetic:
- Dark Slate Obsidian Theme (#070b14, #0f172a)
- Glassmorphic Cards & Glowing Emerald Accents (#10b981, #06b6d4)
- Crisp Corporate Typography & Minimalist Layout Grid
- Zero Emojis (Uses SVG Vector Icons)
- Interactive Date Browsing, PDF Export & Automated Email Dispatch

Run:
    streamlit run app.py
"""

import os
import json
import re
import sys
import subprocess
from pathlib import Path

try:
    import streamlit as st
except ImportError:
    st = None

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from pdf_generator import generate_pdf_from_markdown
from email_service import send_brief_email
from run_pipeline import discover_dates, preflight
from pipeline_status import latest_run

# Clean any emoji from text
def strip_emojis(text: str) -> str:
    return re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf]', '', text)

def main_web_app():
    if st is None:
        print("Streamlit is not installed. Install via: pip install streamlit")
        return

    st.set_page_config(
        page_title="Energy Trading Briefing Portal | Karnataka Southern Grid",
        page_icon="https://svgsilh.com/svg/305245.svg",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom Corporate CSS Theme
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: #070b14 !important;
            color: #f8fafc !important;
        }

        /* Top Brand Navigation Bar */
        .brand-nav {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 18px 24px;
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            margin-bottom: 24px;
        }
        .brand-title {
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            color: #ffffff;
            text-transform: uppercase;
        }
        .brand-subtitle {
            font-size: 0.85rem;
            color: #10b981;
            font-weight: 600;
        }
        .pulse-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #10b981;
            box-shadow: 0 0 10px #10b981;
            margin-right: 8px;
        }

        /* Corporate Hero Metrics Banner */
        .hero-banner {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.7));
            border: 1px solid rgba(16, 185, 129, 0.2);
            border-radius: 12px;
            padding: 28px;
            margin-bottom: 28px;
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
        }
        .hero-headline {
            font-size: 1.8rem;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 8px;
        }
        .hero-desc {
            font-size: 0.95rem;
            color: #94a3b8;
            max-width: 800px;
        }

        /* Card Container Styling */
        .corporate-card {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }

        /* Metric Numbers */
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: #10b981;
        }
        .metric-label {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #64748b;
        }

        /* Buttons & Forms */
        .stButton>button {
            background: linear-gradient(135deg, #059669, #10b981) !important;
            color: #ffffff !important;
            font-weight: 600 !important;
            border: none !important;
            border-radius: 6px !important;
            padding: 10px 20px !important;
            transition: all 0.2s ease !important;
        }
        .stButton>button:hover {
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.4) !important;
            transform: translateY(-1px);
        }

        /* Sidebar Styling */
        section[data-testid="stSidebar"] {
            background-color: #0b1120 !important;
            border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # Corporate Hero Metrics Banner
    st.markdown("""
        <div class="hero-banner">
            <div class="hero-headline">Karnataka Wholesale Power Briefing Portal</div>
            <div class="hero-desc">Source ingestion, cross-domain risk correlation, and statement-level source verification for electricity market operators.</div>
        </div>
    """, unsafe_allow_html=True)

    # Sidebar Controls
    st.sidebar.markdown("<h3 style='color:#f8fafc; font-size:1.1rem; text-transform:uppercase; letter-spacing:0.05em;'>Market Controls</h3>", unsafe_allow_html=True)
    
    dates = discover_dates()
    default_date = dates[-1] if dates else "2026-07-13"

    requested_date = st.query_params.get("date", default_date)
    selected_date = st.sidebar.selectbox("Business Date", options=dates if dates else [default_date], index=dates.index(requested_date) if requested_date in dates else (len(dates)-1 if dates else 0))

    st.sidebar.markdown("---")
    st.sidebar.markdown("<h4 style='color:#94a3b8; font-size:0.9rem;'>Generation Credentials</h4>", unsafe_allow_html=True)
    api_key_input = st.sidebar.text_input("NVIDIA API Key", value=os.environ.get("NVIDIA_API_KEY", ""), type="password", help="Enter key for LLM synthesis")

    if api_key_input:
        os.environ["NVIDIA_API_KEY"] = api_key_input

    st.sidebar.markdown("---")
    require_complete = st.sidebar.checkbox("Require complete source coverage", value=False)
    if st.sidebar.button("Generate briefing"):
        if not os.environ.get("NVIDIA_API_KEY"):
            st.sidebar.error("Add an NVIDIA API key to generate a new briefing.")
        else:
            logs = ROOT / "runs"
            logs.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(ROOT / "run_pipeline.py"), "--date", selected_date]
            if require_complete:
                command.append("--require-complete")
            with (logs / f"{selected_date}_worker.log").open("ab") as log:
                subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            st.sidebar.info("Generation started in the background. Refresh status to see progress.")
    st.sidebar.button("Refresh status")

    readiness = preflight(selected_date)
    with st.expander("Source coverage", expanded=True):
        st.table(readiness["coverage"])
        st.caption("Coverage is relative to the selected business date. Regulatory freshness uses a seven-day collection-window limit; it does not prove availability at market open.")
    recent = latest_run(ROOT, selected_date)
    if recent:
        st.write(f"Latest run: **{recent['status']}** · {recent['started_at']}")
        if recent.get("error"):
            st.error(recent["error"] + ". Check credentials, source coverage, or the local worker log for details.")
        if recent.get("stages"):
            with st.expander("Execution stages", expanded=recent["status"] != "success"):
                st.table(recent["stages"])
        if recent["status"] != "success":
            st.caption("Any briefing below is the previously published version, not output from this run.")

    # Main Grid Layout
    col_brief, col_sidebar = st.columns([2.2, 1])

    verified_md_path = ROOT / "briefs" / f"{selected_date}_verified.md"
    report_json_path = ROOT / "briefs" / f"{selected_date}_verification_report.json"

    with col_brief:
        st.markdown(f"<h3 style='color:#ffffff; font-size:1.3rem; margin-bottom:16px;'>Verified Market Brief ({selected_date})</h3>", unsafe_allow_html=True)
        if verified_md_path.exists():
            raw_text = verified_md_path.read_text(encoding="utf-8")
            clean_md = strip_emojis(raw_text)
            
            # Wrap brief in styled container
            st.markdown(clean_md)
        else:
            st.info("No published briefing for this date. Select Generate briefing to create one.")

    with col_sidebar:
        st.markdown("<h3 style='color:#ffffff; font-size:1.3rem; margin-bottom:16px;'>Audit & Fact Grounding</h3>", unsafe_allow_html=True)

        if report_json_path.exists():
            report_data = json.loads(report_json_path.read_text(encoding="utf-8"))
            summary = report_data.get("summary", {})

            grounding_rate = summary.get("verified_rate_pct")
            total_claims = summary.get("total_claims_checked", 0)
            flagged_claims = summary.get("total_flagged", 0)

            grounding_label = f"{grounding_rate:.1f}%" if isinstance(grounding_rate, (int, float)) else "Not available"
            st.markdown(f"""
                <div class="corporate-card">
                    <div class="metric-label">Source Grounding Rate</div>
                    <div class="metric-value">{grounding_label}</div>
                    <div style="font-size:0.8em; color:#94a3b8; margin-top:4px;">Share of draft claims matched to sources</div>
                </div>
            """, unsafe_allow_html=True)

            if summary.get("verification_method") != "exact_source_statement_v1":
                st.caption("Historical report: legacy numeric checks. Regenerate to apply statement-level verification.")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"""
                    <div class="corporate-card" style="padding:14px;">
                        <div class="metric-label">Claims Checked</div>
                        <div style="font-size:1.4rem; font-weight:700; color:#ffffff;">{total_claims}</div>
                    </div>
                """, unsafe_allow_html=True)
            with c2:
                st.markdown(f"""
                    <div class="corporate-card" style="padding:14px;">
                        <div class="metric-label">Flagged / Cut</div>
                        <div style="font-size:1.4rem; font-weight:700; color:#f43f5e;">{flagged_claims}</div>
                    </div>
                """, unsafe_allow_html=True)

            with st.expander("Inspection Audit Trail"):
                st.json(report_data)
        else:
            st.info("Run verifier agent to populate audit breakdown.")

        st.markdown("---")
        st.markdown("<h4 style='color:#ffffff; font-size:1.1rem; margin-bottom:12px;'>PDF Export & Email Dispatch</h4>", unsafe_allow_html=True)

        # 1. PDF Download
        pdf_path = ROOT / "briefs" / f"{selected_date}_verified.pdf"
        if verified_md_path.exists() and (not pdf_path.exists() or pdf_path.stat().st_mtime < verified_md_path.stat().st_mtime):
            if st.button("Build PDF from published briefing"):
                generate_pdf_from_markdown(verified_md_path.read_text(encoding="utf-8"), str(pdf_path))

        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                st.download_button(
                    label="Download PDF Report",
                    data=f,
                    file_name=f"Energy_Trading_Brief_{selected_date}.pdf",
                    mime="application/pdf"
                )

        # 2. Email Dispatch Form
        st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
        user_email = st.text_input("Recipient Email", placeholder="trader@danske-energy.com")
        if st.button("Send PDF Brief via Email"):
            if user_email:
                with st.spinner("Dispatching report via email..."):
                    sent = send_brief_email(
                        recipient_email=user_email,
                        as_of_date=selected_date,
                        brief_markdown=verified_md_path.read_text(encoding="utf-8") if verified_md_path.exists() else "",
                        attachment_path=str(pdf_path) if pdf_path.exists() else None,
                        verification_summary=report_data.get("summary") if report_json_path.exists() else None
                    )
                    if sent:
                        st.success(f"Report dispatched to {user_email}")
                    else:
                        st.error("Failed to send email. Check SMTP settings.")
            else:
                st.warning("Please enter a valid email address.")

if __name__ == "__main__":
    if st:
        main_web_app()
    else:
        print("Run with streamlit: streamlit run app.py")
