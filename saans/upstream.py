"""One pooled HTTP session shared by every outbound call (reuses TLS connections)."""
from typing import Any

import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))

RequestError = requests.RequestException


def get_json(url: str, params: dict[str, Any], timeout: float) -> Any:
    """GET ``url`` and return the decoded JSON body. Raises ``RequestError``/``ValueError``."""
    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()
