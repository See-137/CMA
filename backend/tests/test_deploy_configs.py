"""Drift guards for the observability deploy configs (PR E).

Dashboards and scrape configs rot silently: a renamed metric leaves a
panel permanently empty with no error anywhere. These tests pin the
deploy tree to the code:

- every cma_* metric referenced in the Grafana dashboard's PromQL must
  exist in app/metrics.py;
- the Prometheus scrape config must target the backend's /metrics with
  the credentials file at the exact path docker-compose mounts;
- the Grafana provisioning chain (datasource uid, dashboards path) must
  be internally consistent with the compose mounts and the dashboard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
DASHBOARD = DEPLOY / "grafana" / "dashboards" / "cma-observability.json"
PROM_CONFIG = DEPLOY / "prometheus" / "prometheus.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
METRICS_SOURCE = (REPO_ROOT / "backend" / "app" / "metrics.py").read_text(
    encoding="utf-8"
)

_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def _dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _all_exprs() -> list[str]:
    return [
        target["expr"]
        for panel in _dashboard()["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    ]


def test_dashboard_metrics_exist_in_code():
    exprs = _all_exprs()
    assert exprs, "dashboard has no queries — provisioning would be empty"

    referenced = set()
    for expr in exprs:
        referenced.update(re.findall(r"\bcma_[a-z0-9_]+", expr))
    assert referenced, "dashboard references no cma_ metrics"

    missing = []
    for name in sorted(referenced):
        base = name
        for suffix in _HISTOGRAM_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base not in METRICS_SOURCE:
            missing.append(name)
    assert not missing, (
        f"dashboard references metrics that do not exist in app/metrics.py: "
        f"{missing} — a renamed metric leaves a panel silently empty"
    )


def test_prometheus_scrape_config_matches_backend_contract():
    config = yaml.safe_load(PROM_CONFIG.read_text(encoding="utf-8"))
    jobs = config["scrape_configs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["metrics_path"] == "/metrics"
    assert job["authorization"]["type"] == "Bearer"
    assert "backend:8000" in job["static_configs"][0]["targets"]

    # The credentials file path must match what docker-compose mounts.
    # Volume entries here are "./host/path:/container/path:ro" with only
    # ./relative host paths, so a substring check on the container path is
    # unambiguous and avoids drive-letter colon parsing entirely.
    creds_path = job["authorization"]["credentials_file"]
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    prom_volumes = compose["services"]["prometheus"]["volumes"]
    assert any(
        f":{creds_path}:" in v or v.endswith(f":{creds_path}") for v in prom_volumes
    ), f"compose does not mount the credentials file at {creds_path}"


def test_grafana_provisioning_chain_is_consistent():
    datasources = yaml.safe_load(
        (
            DEPLOY / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
        ).read_text(encoding="utf-8")
    )
    ds_uid = datasources["datasources"][0]["uid"]

    dashboard = _dashboard()
    panel_uids = {
        panel["datasource"]["uid"]
        for panel in dashboard["panels"]
        if "datasource" in panel
    }
    assert panel_uids == {ds_uid}, (
        f"dashboard panels reference datasource uid(s) {panel_uids}, "
        f"provisioning defines {ds_uid}"
    )

    providers = yaml.safe_load(
        (
            DEPLOY / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
        ).read_text(encoding="utf-8")
    )
    provider_path = providers["providers"][0]["options"]["path"]

    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    grafana_volumes = compose["services"]["grafana"]["volumes"]
    assert any(provider_path in v for v in grafana_volumes), (
        f"compose does not mount dashboards at the provider path {provider_path}"
    )


def test_metrics_token_example_committed_and_real_token_ignored():
    """The example ships; the real token never can (gitignore contract)."""
    assert (DEPLOY / "prometheus" / "metrics-token.example").exists()
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/prometheus/metrics-token" in gitignore
