"""Suite-wide setup that must run BEFORE a test module imports.

One thing happens here, and it happens here because there is nowhere
later that is early enough: `_scope_hygiene.install_guard()` makes the
MACHINE-GLOBAL scope stores unreachable from this process, and records
their contents as the baseline the end-of-run census compares against.

A guard installed inside a single test module protects only the paths
that module happens to import. R-48 required isolation to cover EVERY
test path reaching the ownership API, so the installation sits at the
package that every `tests.test_*` module load passes through.

Its bound, stated: a runner that imports test modules WITHOUT loading
this package (running from inside `tests/` by bare module name) does
not install the guard. The end-of-run census in
`tests/test_ownership.py` is what catches that case, because it
compares the real stores regardless of how the suite was started.
"""

import _scope_hygiene as _hygiene

_hygiene.install_guard()
