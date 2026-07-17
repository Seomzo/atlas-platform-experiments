"""Fail-closed CLI undo coverage for durable Cortex reconciliation."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.memory_manager import MemoryDurabilityError
from cli import HermesCLI


def test_cli_undo_restores_sessiondb_and_keeps_history_on_cortex_failure() -> None:
    cli = HermesCLI.__new__(HermesCLI)
    original_history = [
        {"role": "user", "content": "keep turn"},
        {"role": "assistant", "content": "kept"},
        {"role": "user", "content": "undo turn"},
        {"role": "assistant", "content": "to be removed"},
    ]
    cli.conversation_history = list(original_history)
    cli.session_id = "session-undo"
    cli._session_db = MagicMock()
    cli._session_db.list_recent_user_messages.return_value = [
        {"id": 41, "preview": "undo turn"}
    ]
    cli._session_db.rewind_to_message.return_value = {
        "rewound_count": 2,
        "rewound_message_ids": [41, 42],
        "target_message": {"content": "undo turn"},
    }
    cli._session_db.restore_rewound_ids.return_value = 2
    manager = MagicMock()
    manager.on_session_switch.side_effect = MemoryDurabilityError(
        "cortex", "session switch"
    )
    cli.agent = SimpleNamespace(
        _memory_manager=manager,
        _invalidate_system_prompt=MagicMock(),
        _last_flushed_db_idx=4,
    )

    cli.undo_last(prefill=False)

    assert cli.conversation_history == original_history
    cli._session_db.restore_rewound_ids.assert_called_once_with(
        "session-undo", [41, 42]
    )
    cli.agent._invalidate_system_prompt.assert_not_called()
    assert cli.agent._last_flushed_db_idx == 4
