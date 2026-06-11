"""Regression: rendering a boolean field via sqladmin's widget must not crash on wtforms >= 3.2."""

from sqladmin.widgets import BooleanInputWidget
from wtforms import BooleanField, Form

import ash.admin  # noqa: F401 — importing applies the _compat shim


def test_boolean_input_widget_renders():
    class F(Form):
        enabled = BooleanField(widget=BooleanInputWidget())

    html = str(F().enabled())  # would raise AttributeError without the shim
    assert "checkbox" in html
