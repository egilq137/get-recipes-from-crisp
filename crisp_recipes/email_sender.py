"""Send the weekly recipes as per-recipe PDF attachments.

Credentials and recipients come from `Settings` (env / GitHub secrets) — nothing is
hardcoded. The body is a one-line note; the recipes themselves live only in the PDFs.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from crisp_recipes.models import Recipe

log = logging.getLogger(__name__)


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

    msg.set_content(f"This week's recipes ({week_label}) are attached as PDFs.")

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
