import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path

from .client import API
from .loop import Loop
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Governed outcome optimisation controller")
    parser.add_argument("--url", required=True)
    parser.add_argument("--state-dir", default=os.environ.get("CONTROLLER_STATE_DIR"))
    parser.add_argument("--preferences", default=str(Path(__file__).with_name("preferences.json")))
    parser.add_argument("--dimension", choices=["voice", "welcome_message"], default="voice")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    state_dir = Path(args.state_dir or (".controller-state/" + hashlib.sha256(args.url.encode()).hexdigest()[:12]))
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another controller owns this state directory")
        store = Store(state_dir / "state.db")
        api = API(args.url)
        try:
            preferences = json.loads(Path(args.preferences).read_text())
            Loop(api, store, preferences, args.dimension).run()
        finally:
            api.close()
            store.close()


if __name__ == "__main__":
    main()
