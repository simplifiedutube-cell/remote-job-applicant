"""Job board adapters.

Adding a board: create one file here, decorate the class with `@register_portal`, done.
This package auto-imports every submodule at import time so the decorator fires -- there
is no list to update, and no reinstall.
"""

from __future__ import annotations

import sys

from jobagent.adapters.registry import autodiscover

autodiscover(sys.modules[__name__])
