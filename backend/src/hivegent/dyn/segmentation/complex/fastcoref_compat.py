"""Compatibility shim that makes `fastcoref` usable with `transformers>=5`.

`fastcoref` pins `transformers<5` and its model classes (`FCorefModel`,
`LingMessModel`) never call `PreTrainedModel.post_init()`. In transformers 5,
`post_init()` is what populates the attributes `from_pretrained` relies on
(`all_tied_weights_keys`, the parallelism plans, `_keep_in_fp32_modules`,
`_no_split_modules`), so loading a checkpoint fails with::

    AttributeError: 'FCorefModel' object has no attribute 'all_tied_weights_keys'

We wrap the model `__init__`s so they call `post_init()` at the end, which is
exactly what a transformers 5 model does. On transformers 4 the patch is a
no-op (`post_init()` already exists there and is idempotent, but there is
nothing to fix, so we skip it).

Import this module before instantiating any `fastcoref` model.
"""

from typing import Any

import transformers
from fastcoref.coref_models.modeling_fcoref import FCorefModel
from fastcoref.coref_models.modeling_lingmess import LingMessModel

_PATCH_MARKER = "_dyn_post_init_patched"


def _patch_model_class(cls: type[Any]) -> None:
    if getattr(cls, _PATCH_MARKER, False):
        return

    original_init = cls.__init__

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.post_init()

    cls.__init__ = __init__
    setattr(cls, _PATCH_MARKER, True)


def apply() -> None:
    """Patch the fastcoref model classes (idempotent)."""
    if int(transformers.__version__.split(".")[0]) < 5:
        return
    for cls in (FCorefModel, LingMessModel):
        _patch_model_class(cls)


apply()
