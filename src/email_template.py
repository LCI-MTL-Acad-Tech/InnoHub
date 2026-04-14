"""
email_template.py — generate draft assignment notification emails.
Templates are in Canadian French and Canadian English.
Coordinators are CC'd if assigned to the project.
LinkedIn and portfolio URLs are included when present.
No email is ever sent automatically.
"""

_TEMPLATES = {
    "fr": {
        "subject": "Proposition de stage — {project_title} | {company_name}",
        "body": (
            "Bonjour {student_first}, bonjour {lead_first},\n\n"
            "Nous avons le plaisir de vous proposer un stage au sein de {company_name} "
            "dans le cadre du projet « {project_title} » pour la session {semester}.\n\n"
            "{profile_section}"
            "Veuillez confirmer votre intérêt en répondant à ce courriel.\n\n"
            "Cordialement,"
        ),
        "profile_header": "Profil de l'étudiant·e :\n",
        "linkedin_line":  "  LinkedIn : {url}\n",
        "portfolio_line": "  Portfolio : {url}\n",
    },
    "en": {
        "subject": "Internship proposal — {project_title} | {company_name}",
        "body": (
            "Hello {student_first}, hello {lead_first},\n\n"
            "We are pleased to propose an internship placement at {company_name} "
            "for the project \"{project_title}\" during the {semester} semester.\n\n"
            "{profile_section}"
            "Please confirm your interest by replying to this email.\n\n"
            "Best regards,"
        ),
        "profile_header": "Student profile:\n",
        "linkedin_line":  "  LinkedIn: {url}\n",
        "portfolio_line": "  Portfolio: {url}\n",
    },
}


def _build_profile_section(
    lang: str,
    linkedin_url: str,
    portfolio_urls: list[str],
) -> str:
    """Build the optional profile links block, or empty string if nothing to show."""
    if not linkedin_url and not portfolio_urls:
        return ""

    tmpl   = _TEMPLATES[lang]
    lines  = [tmpl["profile_header"]]

    if linkedin_url:
        lines.append(tmpl["linkedin_line"].format(url=linkedin_url))

    for url in portfolio_urls:
        lines.append(tmpl["portfolio_line"].format(url=url))

    return "".join(lines) + "\n"


def render_email(
    language: str,
    student_name: str,
    student_email: str,
    lead_name: str,
    lead_email: str,
    project_title: str,
    company_name: str,
    semester: str,
    coordinator_emails: list[str] | None = None,
    linkedin_url: str = "",
    portfolio_urls: list[str] | None = None,
) -> dict:
    lang = language if language in _TEMPLATES else "fr"
    tmpl = _TEMPLATES[lang]

    profile_section = _build_profile_section(
        lang, linkedin_url, portfolio_urls or []
    )

    context = {
        "student_first":   student_name.split()[0] if student_name else "",
        "lead_first":      lead_name.split()[0]    if lead_name    else "",
        "project_title":   project_title,
        "company_name":    company_name,
        "semester":        semester,
        "profile_section": profile_section,
    }

    return {
        "to":       [student_email, lead_email],
        "cc":       coordinator_emails or [],
        "subject":  tmpl["subject"].format(**context),
        "body":     tmpl["body"].format(**context),
        "language": lang,
    }
