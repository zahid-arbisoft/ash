"""Compatibility shims for third-party admin dependencies.

sqladmin 0.27.2's ``BooleanInputWidget`` subclasses ``wtforms.widgets.Input`` directly, but in
wtforms >= 3.2 the ``validation_attrs`` attribute is only defined on the concrete ``Input``
subclasses (``TextInput``, ``CheckboxInput``, …) — so rendering a boolean/checkbox field (e.g.
``Integration.enabled``) raises ``AttributeError: 'BooleanInputWidget' object has no attribute
'validation_attrs'``. We restore the attribute (empty = no validation attrs) when it is missing.

Importing this module applies the patch; it is a no-op once sqladmin/wtforms fix the mismatch.
"""

from __future__ import annotations

from sqladmin import widgets as _sqladmin_widgets

if not hasattr(_sqladmin_widgets.BooleanInputWidget, "validation_attrs"):
    _sqladmin_widgets.BooleanInputWidget.validation_attrs = ()
