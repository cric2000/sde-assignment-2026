"""Local read-only API. Run separately; it never advances or mutates the simulator."""

import os

from fastapi import FastAPI

from .inspect import Reader

app = FastAPI(title="Controller supervisor view")


def read(method, *args):
    reader = Reader(os.environ["CONTROLLER_DB"])
    try:
        return getattr(reader, method)(*args)
    finally:
        reader.close()


@app.get("/overview")
def overview():
    return read("overview")


@app.get("/workflows/{workflow}/timeline")
def timeline(workflow: str):
    return read("timeline", workflow)


@app.get("/metrics")
def metrics():
    reader = Reader(os.environ["CONTROLLER_DB"])
    try:
        return {k: v for k, v in reader.meta().items() if not k.startswith(("workflow:", "hallucination_hits:", "caller_hits:"))}
    finally:
        reader.close()
