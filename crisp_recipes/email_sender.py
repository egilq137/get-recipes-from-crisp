"""Send the weekly recipes as an HTML email with per-recipe PDF attachments.

Credentials and recipients come from `Settings` (env / GitHub secrets) — nothing is
hardcoded. The message is multipart: a plain-text summary for text-only clients and
the rich HTML body, plus the PDFs attached.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from crisp_recipes.models import Recipe
from crisp_recipes.render import build_email_html, cooking_time_label

log = logging.getLogger(__name__)


def _plain_text(recipes: List[Recipe], week_label: str) -> str:
    lines = [f"Crisp recipes — {week_label}", ""]
    for r in recipes:
        lines.append(f"• {r.title} — {cooking_time_label(r)}")
        ps = r.nutrition_per_serving()
        if ps is not None:
            cal = ps.get("calories")
            protein = ps.get("protein")
            if cal is not None:
                lines.append(f"    ~{round(cal)} kcal / {round(protein or 0)} g protein per serving")
    lines.append("")
    lines.append("See the HTML version for full nutrition tables and steps.")
    return "\n".join(lines)


def build_message(
    recipes: List[Recipe],
    week_label: str,
    sender: str,
    recipients: List[str],
    pdf_paths: Optional[List[Path]] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Crisp recipes — {week_label}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    msg.set_content(_plain_text(recipes, week_label))
    msg.add_alternative(build_email_html(recipes, week_label), subtype="html")

    for path in pdf_paths or []:
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError:
            log.warning("Could not read attachment %s; skipping", path)
            continue
        msg.add_attachment(
            data, maintype="application", subtype="pdf", filename=path.name
        )
    return msg


def send_email(
    recipes: List[Recipe],
    settings,
    week_label: str,
    pdf_paths: Optional[List[Path]] = None,
) -> None:
    """Build and send the weekly email over Gmail SMTP."""
    msg = build_message(
        recipes,
        week_label,
        sender=settings.sender_email,
        recipients=settings.recipients,
        pdf_paths=pdf_paths,
    )
    with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.sender_email, settings.gmail_app_password)
        server.send_message(msg)
    log.info("Email sent to %s (%d attachments)",
             ", ".join(settings.recipients), len(pdf_paths or []))
