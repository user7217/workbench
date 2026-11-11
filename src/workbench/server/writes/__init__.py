"""Dashboard write path on the server: status edits committed to the data repo.

Every edit goes through `_commit_edit` (in `_transaction`), which holds the repo lock for
the whole operation, edits one YAML file in a fresh worktree cut from origin/<branch>,
validates it (structure, privacy, semantic diff against origin), commits, and pushes with
up to three fetch/rebase/validate/push attempts before resyncing. The Today's Work list
(`set_today`) and its on/off switch (`set_today_enabled`) run the same worktree transaction
(`_run_transaction`) on today.yaml, without the semantic diff: entries leaving that list is normal.
"""

from __future__ import annotations

from workbench.server.writes._transaction import (
    AllowedLearningStatus,
    LearningWriteError,
    WriteError,
)
from workbench.server.writes.fields import (
    update_learning_fields,
    update_learning_item_fields,
    update_learning_item_status,
    update_learning_path_status,
    update_project_fields,
    update_project_item_fields,
    update_project_item_status,
    update_project_status,
)
from workbench.server.writes.structure import (
    add_project_item,
    delete_project_item,
    move_project_item,
    set_project_parent,
)
from workbench.server.writes.suggestions import dismiss_suggestion, keep_suggestion
from workbench.server.writes.today import set_today, set_today_enabled

__all__ = [
    "AllowedLearningStatus",
    "LearningWriteError",
    "WriteError",
    "add_project_item",
    "delete_project_item",
    "dismiss_suggestion",
    "keep_suggestion",
    "move_project_item",
    "set_project_parent",
    "set_today",
    "set_today_enabled",
    "update_learning_fields",
    "update_learning_item_fields",
    "update_learning_item_status",
    "update_learning_path_status",
    "update_project_fields",
    "update_project_item_fields",
    "update_project_item_status",
    "update_project_status",
]
