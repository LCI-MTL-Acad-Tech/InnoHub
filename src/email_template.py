"""
email_template.py — generate draft assignment notification emails.
Templates are in Canadian French and Canadian English.
No email is ever sent automatically.
"""

_TEMPLATES = {
    "fr": {
        "subject": "Proposition de stage — {project_title} | {company_name}",
        "body": (
            "Bonjour {student_first}, bonjour {lead_first},\n\n"
            "Nous avons le plaisir de vous proposer un stage au sein de {company_name} "
            "dans le cadre du projet « {project_title} » pour la session {semester}.\n\n"
            "Veuillez confirmer votre intérêt en répondant à ce courriel.\n\n"
            "Cordialement,"
        ),
    },
    "en": {
        "subject": "Internship proposal — {project_title} | {company_name}",
        "body": (
            "Hello {student_first}, hello {lead_first},\n\n"
            "We are pleased to propose an internship placement at {company_name} "
            "for the project \"{project_title}\" during the {semester} semester.\n\n"
            "Please confirm your interest by replying to this email.\n\n"
            "Best regards,"
        ),
    },
}


def render_email(
    language: str,
    student_name: str,
    student_email: str,
    lead_name: str,
    lead_email: str,
    project_title: str,
    company_name: str,
    semester: str,
) -> dict:
    lang = language if language in _TEMPLATES else "fr"
    tmpl = _TEMPLATES[lang]

    context = {
        "student_first": student_name.split()[0],
        "lead_first":    lead_name.split()[0],
        "project_title": project_title,
        "company_name":  company_name,
        "semester":      semester,
    }

    return {
        "to":      [student_email, lead_email],
        "subject": tmpl["subject"].format(**context),
        "body":    tmpl["body"].format(**context),
        "language": lang,
    }
