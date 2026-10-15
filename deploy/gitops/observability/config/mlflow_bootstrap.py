"""Create MLflow's users and grant each one only what it needs on experiment 0.

* collector: EDIT, so the OpenTelemetry Collector can write traces there; nothing else.
* a personal account (USER_NAME, when set): READ, for looking at traces without the admin account.

New users get no access by default (basic_auth.ini sets default_permission = NO_PERMISSIONS), so these grants are the
whole of what they can do. Runs as a Job in the MLflow image after MLflow is up. Idempotent: an existing user keeps its
password, and a grant it already has is a no-op. Reads the passwords from the mlflow-secrets Secret.
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


def ensure_user(username, password):
    status, text = call("POST", "/api/2.0/mlflow/users/create", {"username": username, "password": password})
    if status == 200:
        print(f"created user {username}")
    elif "RESOURCE_ALREADY_EXISTS" in text or "already exists" in text:
        print(f"user {username} exists")
    else:
        sys.exit(f"creating {username}: HTTP {status} {text[:300]}")


def grant(username, permission):
    status, text = call("POST", "/api/3.0/mlflow/users/permissions/grant",
                        {"username": username, "resource_type": "experiment", "resource_id": EXPERIMENT,
                         "permission": permission})
    if status != 200:
        sys.exit(f"granting {permission} on experiment {EXPERIMENT} to {username}: HTTP {status} {text[:300]}")
    query = f"?username={username}&resource_type=experiment&resource_id={EXPERIMENT}"
    status, text = call("GET", f"/api/3.0/mlflow/users/permissions/get{query}")
    print(f"{username} on experiment {EXPERIMENT}: HTTP {status} {text[:200]}")
    if status != 200 or json.loads(text).get("permission") != permission:
        sys.exit(f"{username} does not have {permission} on the experiment")


def main():
    wait_for_mlflow()
    ensure_user(COLLECTOR, os.environ["COLLECTOR_PASSWORD"])
    grant(COLLECTOR, "EDIT")
    person, password = os.environ.get("USER_NAME"), os.environ.get("USER_PASSWORD")
    if person and password:
        ensure_user(person, password)
        grant(person, os.environ.get("USER_PERMISSION", "READ"))


if __name__ == "__main__":
    main()
