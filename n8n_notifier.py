"""
n8n_notifier -- POST a run summary to an n8n webhook on completion.

Designed never to raise: if the webhook is misconfigured or n8n is down, the
scraper run is still considered successful (data is already in Sheets).
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

import requests

from config import N8nConfig
from utils import retry_with_backoff

logger = logging.getLogger(__name__)


def notify_n8n(cfg: N8nConfig, payload: Mapping[str, Any]) -> bool:
    """
    POST *payload* as JSON to the configured n8n webhook.

    Returns True if n8n acknowledged with 2xx, False otherwise (including if
    the webhook is disabled). Never raises.
    """
    if not cfg.enabled:
        logger.info("n8n webhook disabled (N8N_WEBHOOK_URL not set); skipping notify.")
        return False

    headers = {"Content-Type": "application/json"}
    if cfg.webhook_token:
        headers["X-Auth-Token"] = cfg.webhook_token

    def _post() -> requests.Response:
        response = requests.post(
            cfg.webhook_url,
            json=dict(payload),
            headers=headers,
            timeout=cfg.timeout,
        )
        response.raise_for_status()
        return response

    try:
        resp = retry_with_backoff(
            _post,
            max_retries=3,
            base_delay=1.0,
            exceptions=(requests.RequestException,),
            logger=logger,
        )
        logger.info("n8n notify succeeded (status=%d).", resp.status_code)
        return True
    except requests.RequestException as exc:
        logger.error("n8n notify failed after retries: %s", exc)
        return False
    except Exception as exc:
        logger.error("n8n notify failed with unexpected error: %s", exc)
        return False
