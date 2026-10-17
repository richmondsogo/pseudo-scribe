"""Data models and synchronization primitives for course state management."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from pseudoscribe.config import get_courses_dir


def course_path(course_id: str) -> Path:
    """Return the filesystem path for a given course ID."""
    return get_courses_dir() / course_id


@dataclass
class Course:
    """Represents a course directory and all its associated standard artifact paths."""

    id: str
    path: Path
    profile_path: Path
    outline_path: Path
    terminology_path: Path
    generation_state_path: Path
    prompts_path: Path
    drafts_path: Path
    tex_path: Path
    final_path: Path
    output_pdf: Path

    @classmethod
    def load(cls, course_id: str) -> Course:
        """Initialize a Course instance with standard path mappings."""
        p = course_path(course_id)
        return cls(
            id=course_id,
            path=p,
            profile_path=p / "course_profile.md",
            outline_path=p / "outline.json",
            terminology_path=p / "terminology.json",
            generation_state_path=p / "generation_state.json",
            prompts_path=p / "prompts",
            drafts_path=p / "drafts",
            tex_path=p / "tex",
            final_path=p / "final",
            output_pdf=p / "output.pdf",
        )


# Synchronization locks for multi-threaded execution
STATE_LOCK: threading.Lock = threading.Lock()
TERMINOLOGY_LOCK: threading.Lock = threading.Lock()
_LLM_CALL_COUNT_LOCK: threading.Lock = threading.Lock()
LLM_CALL_COUNT: int = 0


def require_course_files(c: Course) -> None:
    """Validate required course files exist, initializing empty state/terminology if absent."""
    from pseudoscribe.utils import save_json

    if not c.path.exists():
        raise FileNotFoundError(f"Course not found: {c.path}")
    if not c.outline_path.exists():
        raise FileNotFoundError(f"Missing outline.json at {c.outline_path}")
    if not c.profile_path.exists():
        raise FileNotFoundError(f"Missing course_profile.md at {c.profile_path}")
    if not c.terminology_path.exists():
        save_json(c.terminology_path, {"terms": {}})
    if not c.generation_state_path.exists():
        save_json(
            c.generation_state_path,
            {
                "status": "in_progress",
                "chapters": {},
                "final_qa": False,
                "compiled": False,
            },
        )
