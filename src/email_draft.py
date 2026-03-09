"""
email_draft.py — Generate draft email text for assignment notifications.

Two templates: Canadian French and Canadian English.
Language is determined by the detected_language field of the project.
"""

from __future__ import annotations


TEMPLATES = {
    "fr": {
        "subject": "Proposition de stage — {project_title} | {company_name}",
        "body": (
            "Objet\u00a0: Proposition de stage — {project_title} | {company_name}\n\n"
            "Bonjour {student_first_name}, bonjour {lead_first_name},\n\n"
            "Nous avons le plaisir de vous proposer un stage au sein de {company_name} "
            "dans le cadre du projet «\u00a0{project_title}\u00a0» pour la session {semester}.\n\n"
            "Veuillez confirmer votre intérêt en répondant à ce courriel.\n\n"
            "Cordialement,"
        ),
    },
    "en": {
        "subject": "Internship proposal — {project_title} | {company_name}",
        "body": (
            "Subject: Internship proposal — {project_title} | {company_name}\n\n"
            "Dear {student_first_name}, dear {lead_first_name},\n\n"
            "We are pleased to propose an internship placement at {company_name} "
            "as part of the project \"{project_title}\" for the {semester} term.\n\n"
            "Please confirm your interest by replying to this email.\n\n"
            "Best regards,"
        ),
    },
}


def _first_name(full_name: str) -> str:
    """Best-effort extraction of first name."""
    parts = full_name.strip().split()
    return parts[0] if parts else full_name


def _lead_first_name(email: str) -> str:
    """
    Extract a plausible first name from an email address as fallback
    when we don't have the lead's full name stored.
    e.g. jean.tremblay@example.com → Jean
    """
    local = email.split("@")[0]
    part = local.split(".")[0].split("_")[0]
    return part.capitalize()


def generate_draft(
    student_name: str,
    student_email: str,
    lead_email: str,
    project_title: str,
    company_name: str,
    semester: str,
    language: str,
    lead_name: str | None = None,
) -> dict[str, str]:
    """
    Return a dict with keys: to_student, to_lead, subject, body.
    language must be 'fr' or 'en'; falls back to 'fr'.
    """
    lang = language if language in TEMPLATES else "fr"
    tmpl = TEMPLATES[lang]

    student_first = _first_name(student_name)
    lead_first = _first_name(lead_name) if lead_name else _lead_first_name(lead_email)

    body = tmpl["body"].format(
        student_first_name=student_first,
        lead_first_name=lead_first,
        company_name=company_name,
        project_title=project_title,
        semester=semester,
    )
    subject = tmpl["subject"].format(
        project_title=project_title,
        company_name=company_name,
    )

    return {
        "to_student": student_email,
        "to_lead":    lead_email,
        "subject":    subject,
        "body":       body,
        "language":   lang,
    }


def format_for_display(draft: dict[str, str]) -> str:
    """Render the draft for terminal display."""
    lang_label = "French" if draft["language"] == "fr" else "English"
    return (
        f"\nDetected language: {lang_label}\n\n"
        f"{'─' * 57}\n"
        f"TO:      {draft['to_student']}\n"
        f"TO:      {draft['to_lead']}\n"
        f"{'─' * 57}\n"
        f"{draft['body']}\n"
        f"{'─' * 57}\n"
    )
