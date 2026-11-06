from controller.inspect import Reader
from controller.model import dumps


def test_why_reports_supervisor_change_instead_of_older_controller_decision(store):
    with store.transaction():
        store.set("sim_minute", 300)
        event = {"actor": "supervisor", "version": 3, "workflow_id": "test", "changed_dims": ["voice"], "proposal_id": None}
        store.db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (1, 200, "test", "config_changed", dumps(event)))
    reader = Reader(store.path)
    try:
        result = reader.why("test")
        assert result["authority"] == "client supervisor"
        assert result["change"]["version"] == 3
        assert result["rationale"] == "not provided by the HTTP contract"
    finally:
        reader.close()
