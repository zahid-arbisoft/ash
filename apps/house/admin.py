from django.contrib import admin

from .models import Client, Project, Run


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("config_name", "client", "name", "created_at")
    list_filter = ("client",)


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "issue_number",
        "issue_title",
        "stage",
        "status",
        "pr_url",
        "created_at",
    )
    list_filter = ("project", "stage", "status")
    search_fields = ("issue_number", "issue_title", "branch", "pr_url")
    readonly_fields = ("state", "created_at", "updated_at")
