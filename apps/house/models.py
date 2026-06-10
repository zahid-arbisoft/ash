"""Control-plane persistence — the multi-tenant domain model (plan §0).

    Client (tenant)  ──<  Project (engagement)  ──<  Run (one pipeline execution)

The engine itself stays config/file-driven; these tables give the platform durable, queryable
records of who asked for what and what happened — the foundation for parallel multi-tenant
engagements, oversight, and (later) a control-plane API/UI.
"""

from __future__ import annotations

from django.db import models


class Client(models.Model):
    """A tenant: a human/organization the software house works for."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Project(models.Model):
    """An engagement — maps 1:1 to a projects/<config_name>.yaml today."""

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=200)
    config_name = models.CharField(
        max_length=100, unique=True, help_text="Name of projects/<config_name>.yaml"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.client.slug}/{self.config_name}"


class Run(models.Model):
    """One execution of the build pipeline for one issue (persisted WorkflowState)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="runs")
    issue_number = models.IntegerField()
    issue_title = models.CharField(max_length=500, blank=True)
    branch = models.CharField(max_length=300, blank=True)
    pr_url = models.URLField(blank=True)
    stage = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=40, blank=True)
    error = models.TextField(blank=True)
    state = models.JSONField(default=dict, help_text="Full serialized WorkflowState")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.project.config_name}#{self.issue_number} [{self.stage}/{self.status}]"
