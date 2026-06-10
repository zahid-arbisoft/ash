from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("", admin.site.urls),  # admin is the control-plane UI for now (REST API comes later)
]
