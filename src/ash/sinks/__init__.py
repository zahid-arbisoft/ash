"""Task sinks — destinations for PM-generated tickets (file board / Jira / Plane / …)."""

from ash.sinks.base import TicketRef, TicketSink
from ash.sinks.file import FileBoardSink
from ash.sinks.jira import JiraTaskSink
from ash.sinks.plane import PlaneTaskSink
from ash.sinks.service import (
    build_sink,
    create_task_sink,
    get_default_sink,
    list_task_sinks,
    resolve_task_sink,
)

__all__ = [
    "FileBoardSink",
    "JiraTaskSink",
    "PlaneTaskSink",
    "TicketRef",
    "TicketSink",
    "build_sink",
    "create_task_sink",
    "get_default_sink",
    "list_task_sinks",
    "resolve_task_sink",
]
