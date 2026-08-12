"""What we know about a customer's databases (architecture Part 5.2, 5.3).

* ``discovery`` — the metadata pass: catalog queries only, no table scans, and a
  snapshot only when something actually changed (DECISIONS D-012).

The profiling and classification passes (WP4.2) and table cards (WP4.3) join
this package as their work packages land.
"""

from __future__ import annotations
