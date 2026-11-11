"""Applicant-tracking-system adapters -- the deterministic apply lanes.

Greenhouse, Lever, Ashby and Workday have stable, predictable DOM, which makes a scripted
adapter roughly fifty times cheaper and far more reliable than a computer-use loop. The
browser agent is reserved for genuinely unknown sites.
"""

from __future__ import annotations

import sys

from jobagent.adapters.registry import autodiscover

autodiscover(sys.modules[__name__])
