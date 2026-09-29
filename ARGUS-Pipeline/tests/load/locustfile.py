"""Load test for the ARGUS API (PRD: entity search p95 < 3 s).

    locust -f tests/load/locustfile.py --headless -u 50 -r 5 --run-time 60s --host http://localhost:8000

Uses the seeded demo accounts (override with ARGUS_LOAD_INVESTIGATOR / ARGUS_LOAD_ANALYST="id:password").
The run exits non-zero if p95 of any search endpoint exceeds SEARCH_P95_MS (default 3000) or more than 1% of
requests fail, so it can gate a pipeline.

NOTE: every request is audited, so a run adds rows to the (append-only) audit log. Point it at a test stack.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, constant, events, task

SEARCH_P95_MS = int(os.getenv("SEARCH_P95_MS", "3000"))
MAX_FAILURE_RATIO = float(os.getenv("MAX_FAILURE_RATIO", "0.01"))
SEARCH_TERMS = ["Police", "Khan", "Sharma", "trafficking", "Market", "Singh", "Kumar", "Station"]
# Endpoint names (the `name=` given to each request below) whose latency is held to the search target.
SEARCH_ENDPOINTS = ("search/firs", "search/master-dossier", "network/accused")


def _credentials(env: str, default: str) -> tuple[str, str]:
    employee_id, _, password = os.getenv(env, default).partition(":")
    return employee_id, password


class _ArgusUser(HttpUser):
    abstract = True
    credentials = ("INV001", "demo123")
    names: list[str]

    def on_start(self) -> None:
        # One sign-in per simulated officer; the repeating loop then runs on the session token.
        employee_id, password = self.credentials
        with self.client.post("/api/v1/auth/login", json={"employee_id": employee_id, "password": password},
                              name="auth/login", catch_response=True) as response:
            if response.status_code != 200 or "access_token" not in response.json():
                response.failure(f"login failed: {response.status_code}")
                self.environment.runner.quit()
                return
            self.client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        self.names = ["Imran Khan", "Mohit Chadha", "Vikram Singh"]  # replaced with real names as searches return them

    def _remember(self, results: list[dict]) -> None:
        found = [r["accused"] for r in results if r.get("accused")]
        if found:
            self.names = list(dict.fromkeys(found + self.names))[:20]

    def _search(self, term: str) -> None:
        with self.client.get("/api/v1/search/firs", params={"q": term}, name="search/firs", catch_response=True) as r:
            if r.status_code == 200:
                self._remember(r.json().get("results", []))
            else:
                r.failure(f"HTTP {r.status_code} {type(getattr(r, 'error', None)).__name__}: {str(getattr(r, 'error', ''))[:90]}")

    def _network(self, name: str) -> None:
        self.client.get("/api/v1/network/accused", params={"accused_name": name}, name="network/accused")

    def _entity_detail(self, name: str) -> None:
        # The entity page loads the dossier, then the person's network.
        self.client.get("/api/v1/search/master-dossier", params={"query": name}, name="search/master-dossier")
        self._network(name)


class InvestigatorBehavior(_ArgusUser):
    """login -> 5 FIR searches -> 2 accused networks -> case list -> wait 5 s -> repeat"""

    weight = 3
    credentials = _credentials("ARGUS_LOAD_INVESTIGATOR", "INV001:demo123")
    wait_time = constant(5)

    @task
    def working_session(self) -> None:
        for term in random.sample(SEARCH_TERMS, 5):
            self._search(term)
        for name in random.sample(self.names, k=min(2, len(self.names))):
            self._network(name)
        self.client.get("/api/v1/cases", name="cases")


class AnalystBehavior(_ArgusUser):
    """login -> patterns -> centrality -> 3 entity details -> repeat"""

    weight = 2
    credentials = _credentials("ARGUS_LOAD_ANALYST", "ANL001:demo123")
    wait_time = between(1, 3)

    @task
    def analysis_session(self) -> None:
        self.client.get("/api/v1/patterns", name="patterns")
        self.client.get("/api/v1/analytics/centrality", name="analytics/centrality")
        for name in random.sample(self.names, k=min(3, len(self.names))):
            self._entity_detail(name)
        self._search(random.choice(SEARCH_TERMS))


@events.quitting.add_listener
def _enforce_targets(environment, **_kwargs) -> None:
    total = environment.stats.total
    failed_ratio = total.fail_ratio
    problems = []
    for name in SEARCH_ENDPOINTS:
        entry = next((e for (n, _m), e in environment.stats.entries.items() if n == name), None)
        if entry and entry.num_requests:
            p95 = entry.get_response_time_percentile(0.95)
            print(f"[target] {name}: p95 = {p95:.0f} ms over {entry.num_requests} requests (limit {SEARCH_P95_MS} ms)")
            if p95 > SEARCH_P95_MS:
                problems.append(f"{name} p95 {p95:.0f} ms > {SEARCH_P95_MS} ms")
    if failed_ratio > MAX_FAILURE_RATIO:
        problems.append(f"failure ratio {failed_ratio:.1%} > {MAX_FAILURE_RATIO:.0%}")
    if problems:
        print("[target] FAILED: " + "; ".join(problems))
        environment.process_exit_code = 1
    else:
        print("[target] PASSED")
        environment.process_exit_code = 0   # a few failed requests (<= MAX_FAILURE_RATIO) are tolerated by design
