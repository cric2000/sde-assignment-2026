from datetime import datetime, timedelta, timezone

import pytest

from controller.ingest import Ingester
from controller.model import timestamp_minute

START = datetime(2027, 1, 4, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def event(eid, kind="call", **kwargs):
    body = {"event_id": eid, "type": kind, "ts": START.isoformat(), "workflow_id": "test"}
    if kind == "call":
        body.update(lead_id="lead1", person_id="person1", campaign_id="campaign1", config_version=1,
                    cohort_id="default", variant={"voice": "V1", "welcome_message": "W1", "caller_id": "D1", "prompt_version": "P1"},
                    connected=True, engaged=True, qa={"hallucination_flag": False})
    elif kind == "lead":
        body.update(lead_id="lead1", person_id="person1", campaign_id="campaign1", features={"lang": "en"})
    body.update(kwargs)
    return body


def test_replay_partial_batches_and_late_feature_join(store):
    origin = timestamp_minute(START.isoformat())
    store.ingest([event(1)], origin)
    store.ingest([event(1), event(2, "lead")], origin)
    store.ingest([event(1), event(2, "lead")], origin)
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert store.db.execute("SELECT SUM(dials),SUM(engaged) FROM rollups").fetchone()[:] == (1, 1)
    assert store.db.execute("SELECT concluded FROM leads").fetchone()[0] == 1


def test_failed_batch_rolls_back_checkpoint_and_aggregates(store):
    with pytest.raises(KeyError):
        store.ingest([event(1), {"event_id": 2, "type": "call"}], 0)
    assert store.get("event_watermark") is None
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    store.ingest([event(1)], 0)
    assert store.get("event_watermark") == 1


def test_both_chunk_kinds_are_merged_in_global_order(store):
    import json
    class API:
        def get(self, path, kind, after):
            return [{"chunk": 1}] if kind == "history" else [{"chunk": 0}]

        def lines(self, chunk):
            return [json.dumps(event(chunk + 1))]
    ingester = Ingester(API(), store, batch_size=1)
    ingester.drain()
    ingester.drain()
    assert store.get("events_ingested") == 2
    assert store.db.execute("SELECT COUNT(*) FROM chunks WHERE done=1").fetchone()[0] == 2


def test_out_of_order_batch_is_rejected(store):
    with pytest.raises(ValueError):
        store.ingest([event(2), event(1)], 0)
