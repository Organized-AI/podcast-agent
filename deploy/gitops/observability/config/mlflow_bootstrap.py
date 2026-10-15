"""Give the Collector its own MLflow user: it may edit experiment 0 (write traces there) and nothing else.

Runs as a Job in the MLflow image after MLflow is up. Idempotent: an existing user keeps its password, and granting a
permission it already has is a no-op. Reads ADMIN_PASSWORD and COLLECTOR_PASSWORD from the mlflow-secrets Secret.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("MLFLOW_URL", "http://mlflow:5000")
ADMIN = ("admin", os.environ["ADMIN_PASSWORD"])
COLLECTOR = os.environ.get("COLLECTOR_USERNAME", "collector")
EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT_ID", "0")


def call(method, path, body=None, auth=ADMIN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_for_mlflow(timeout_s=600):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(5)
    sys.exit(f"MLflow at {URL} did not become healthy in {timeout_s}s")


def main():
    wait_for_mlflow()
    status, text = call("POST", "/api/2.0/mlflow/users/create",
                        {"username": COLLECTOR, "password": os.environ["COLLECTOR_PASSWORD"]})
    if status == 200:
        print(f"created user {COLLECTOR}")
    elif "RESOURCE_ALREADY_EXISTS" in text or "already exists" in text:
        print(f"user {COLLECTOR} exists")
    else:
        sys.exit(f"creating {COLLECTOR}: HTTP {status} {text[:300]}")

    status, text = call("POST", "/api/3.0/mlflow/users/permissions/grant",
                        {"username": COLLECTOR, "resource_type": "experiment", "resource_id": EXPERIMENT,
                         "permission": "EDIT"})
    if status != 200:
        sys.exit(f"granting EDIT on experiment {EXPERIMENT}: HTTP {status} {text[:300]}")

    query = f"?username={COLLECTOR}&resource_type=experiment&resource_id={EXPERIMENT}"
    status, text = call("GET", f"/api/3.0/mlflow/users/permissions/get{query}")
    print(f"{COLLECTOR} on experiment {EXPERIMENT}: HTTP {status} {text[:200]}")
    if status != 200 or json.loads(text).get("permission") != "EDIT":
        sys.exit("the collector does not have EDIT on the experiment")


if __name__ == "__main__":
    main()
