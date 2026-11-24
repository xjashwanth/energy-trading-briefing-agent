"""
Email Service Module for Energy Trading Briefing Agent

Automates email delivery of verified daily trading briefs (with PDF attachments)
to energy traders and market participants.

Design: Corporate Energy Trading Theme (Zero Emojis).
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

def send_brief_email(
    recipient_email: str,
    as_of_date: str,
    brief_markdown: str,
    attachment_path: str = None,
    verification_summary: dict = None
) -> bool:
    """
    Sends the verified trading brief to recipient_email via SMTP.
    If SMTP environment variables are missing, runs in dry-run mode and logs output.
    """
    smtp_server = os.environ.get("SMTP_SERVER", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    sender_email = os.environ.get("SENDER_EMAIL", smtp_user or "noreply@energy-trading-agent.org")

    subject = f"Daily Energy Trading Brief - Karnataka (Southern Grid) [{as_of_date}]"

    rate = (verification_summary or {}).get("verified_rate_pct")
    grounding_label = f"Source grounding: {rate:.1f}% of draft claims" if isinstance(rate, (int, float)) else "Source grounding: not available"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', Arial, sans-serif; color: #1e293b; background-color: #f8fafc; line-height: 1.6; max-width: 650px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #0f172a; border-left: 4px solid #10b981; color: #ffffff; padding: 24px; border-radius: 6px 6px 0 0; }}
            .content {{ background: #ffffff; padding: 24px; border: 1px solid #e2e8f0; border-radius: 0 0 6px 6px; }}
            .badge {{ background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; padding: 6px 12px; border-radius: 4px; font-weight: 600; font-size: 0.85em; display: inline-block; margin-top: 10px; }}
            .footer {{ margin-top: 24px; font-size: 0.8em; color: #64748b; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="margin:0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;">KARNATAKA ENERGY MARKET OPERATIONS</h2>
            <p style="margin:6px 0 0 0; color: #94a3b8; font-size: 13px;">Southern Regional Grid Briefing Portal â€¢ {as_of_date}</p>
        </div>
        <div class="content">
            <p>Hello,</p>
            <p>The verified daily energy trading briefing report for <strong>{as_of_date}</strong> is attached.</p>
            <div class="badge">{grounding_label}</div>
            <hr style="border:0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p>Please find attached the official PDF report containing cross-referenced market indicators, plant outage status, weather load impact, and regulatory summaries.</p>
            <p style="font-size:0.85em; color:#64748b;"><em>Trade Boundary Disclaimer: This briefing is produced for market risk awareness. It does not contain price forecasts or trade execution recommendations.</em></p>
        </div>
        <div class="footer">
            Energy Trading Briefing System â€¢ PS130 â€¢ Dayananda Sagar University
        </div>
    </body>
    </html>
    """

    if not (smtp_server and smtp_user and smtp_password):
        print(f"\n[EMAIL SERVICE - DRY RUN MODE]")
        print(f"Recipient: {recipient_email}")
        print(f"Subject: {subject}")
        print(f"Attachment: {attachment_path if attachment_path else 'None'}")
        print("Note: Set SMTP_SERVER, SMTP_USERNAME, and SMTP_PASSWORD in environment to send real emails.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg["Subject"] = subject

        msg.attach(MIMEText(html_body, "html"))

        if attachment_path and Path(attachment_path).exists():
            att_file = Path(attachment_path)
            with open(att_file, "rb") as f:
                part = MIMEApplication(f.read(), Name=att_file.name)
            part['Content-Disposition'] = f'attachment; filename="{att_file.name}"'
            msg.attach(part)

        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(sender_email, [recipient_email], msg.as_string())

        print(f"[EMAIL SERVICE SUCCESS] Brief sent to {recipient_email}")
        return True

    except Exception as e:
        print(f"[EMAIL SERVICE ERROR] Failed to send email: {e}")
        return False
