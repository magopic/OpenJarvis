"""Read-only projections derived from the governed Second Brain.

Every projection in this package is one-way: it *reads* through
``SecondBrainService`` (never ``SecondBrainStore`` directly, and never
raw SQL) and writes somewhere else -- a filesystem, an API response,
whatever the specific projection is. None of them accept writes back
into the Second Brain. Kept outside the frozen core
(``second_brain/service.py``/``store.py``/``types.py``) on purpose --
a projection is a *consumer* of the governed API, not part of it.
"""

from __future__ import annotations

__all__: list[str] = []
