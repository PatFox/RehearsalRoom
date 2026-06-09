# Runtime hook — fixes numpy.core.multiarray missing in NumPy 2.x.
#
# PyTorch model weights pickled against older NumPy reference
# numpy.core.multiarray directly. In NumPy 2.x the module moved to
# numpy._core.multiarray. We re-expose it at the old path so unpickling works.

import sys
import types

try:
    import numpy.core.multiarray  # already present (NumPy 1.x) — nothing to do
except ImportError:
    import numpy
    import numpy._core.multiarray as _src

    # Ensure numpy.core exists as a package
    if "numpy.core" not in sys.modules:
        core_mod = types.ModuleType("numpy.core")
        core_mod.__package__ = "numpy.core"
        core_mod.__path__ = []
        sys.modules["numpy.core"] = core_mod
        numpy.core = core_mod  # type: ignore[attr-defined]

    # Expose multiarray at the old path
    ma_mod = types.ModuleType("numpy.core.multiarray")
    ma_mod.__dict__.update({
        k: v for k, v in _src.__dict__.items() if not k.startswith("__")
    })
    sys.modules["numpy.core.multiarray"] = ma_mod
