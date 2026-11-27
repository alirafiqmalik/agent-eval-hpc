"""Harbor Modal sandboxes that a Modal Function starts for one run."""

from __future__ import annotations

import os

from harbor.environments.modal import ModalEnvironment

RUN_TAG = "agentic-eval.run"


class ModalRunEnvironment(ModalEnvironment):
    """Put trial sandboxes in the run's app and tag them with the run identity.

    Harbor 0.24 preflight requires ~/.modal.toml or MODAL_TOKEN_ID. A Modal
    Function has neither: it authenticates with its own task identity. The tag
    lets cancel and cleanup find every sandbox that belongs to one run.
    """

    @classmethod
    def preflight(cls) -> None:
        if os.environ.get("MODAL_IS_REMOTE") == "1":
            return
        super().preflight()

    def __init__(self, *args, **kwargs):
        if app_name := os.environ.get("EVAL_MODAL_APP"):
            kwargs["app_name"] = app_name
        if run_id := os.environ.get("EVAL_RUN_ID"):
            kwargs["labels"] = {**(kwargs.get("labels") or {}), RUN_TAG: run_id}
        super().__init__(*args, **kwargs)
