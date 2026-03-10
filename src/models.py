"""
models.py — dataclasses mirroring the JSON schemas and CSV rows.
All persistence logic lives in store.py; these are pure data containers.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Document:
    type: str          # cv | cover_letter | company_description | project_proposal
    filename: str
    ingested_date: str


@dataclass
class Task:
    task_id: str
    label: str
    hours: int


@dataclass
class Capacity:
    total_hours: int
    tasks: list[Task]


@dataclass
class Student:
    student_number: str
    name: str
    email: str
    program: str
    semester_start: str
    hours_available: int
    status: str        # active | inactive | completed
    reassignment_history: list[dict] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    embedding_file: str = ""
    notes: str = ""


@dataclass
class Company:
    company_id: str
    name: str
    status: str        # active | inactive
    language: str      # fr | en
    contact_name: str
    contact_email: str
    activation_history: list[dict] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    embedding_file: str = ""
    notes: str = ""


@dataclass
class Coordinator:
    coordinator_id: str
    name: str
    email: str
    programs: list[str]        # empty list means all programs
    status: str                # active | inactive
    documents: list[Document] = field(default_factory=list)
    embedding_file: str = ""
    notes: str = ""


@dataclass
class Project:
    project_id: str
    company_id: str
    title: str
    status: str        # active | inactive | closed
    semester: str
    language: str      # fr | en
    capacity: Capacity
    lead_name: str
    lead_email: str
    renewal_history: list[dict] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    coordinators: list[str] = field(default_factory=list)  # coordinator_ids
    embedding_file: str = ""
    notes: str = ""


@dataclass
class AssignmentRow:
    """One row in assignments.csv — one per task per assignment."""
    assignment_id: str
    student_number: str
    student_email: str
    student_program: str
    project_id: str
    project_lead_email: str
    semester: str
    task_id: str
    task_label: str
    hours_planned: int
    hours_committed: int
    status: str        # proposed | confirmed | completed | cancelled
    assigned_date: str
    confirmed_date: str = ""
    completed_date: str = ""
    notes: str = ""


@dataclass
class TermWeight:
    term: str
    student_weight: float
    project_weight: float
    shared_weight: float


@dataclass
class Explanation:
    student_number: str
    project_id: str
    score: float
    shared_terms: list[TermWeight]
    student_only_terms: list[str]
    project_only_terms: list[str]


# Valid statuses
STUDENT_STATUSES     = {"active", "inactive", "completed"}
COMPANY_STATUSES     = {"active", "inactive"}
PROJECT_STATUSES     = {"active", "inactive", "closed"}
COORDINATOR_STATUSES = {"active", "inactive"}
ASSIGNMENT_STATUSES  = {"proposed", "confirmed", "completed", "cancelled"}
