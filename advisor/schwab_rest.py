"""Read-only Schwab Trader API client on plain `requests` (no schwab-py).

The droplet image pins exactly one third-party package (requests) for
supply-chain reasons, and the daily IRA review only needs two GETs: the
account list and the positions. So this speaks the REST API directly and
reads the token file schwab-py writes on the Mac
({"creation_timestamp": ..., "token": {...}}), refreshing the 30-minute access
token with the 7-day refresh token and writing the refreshed token back.

There is deliberately NO order method here. Orders for the IRA go through
schwab_client.py / cli.py on the Mac, one at a time, with a typed
confirmation.

Schwab's refresh token expires 7 days after the interactive login; when it
has, `refresh()` raises TokenExpired and the review falls back to its last
cached snapshot and says so (see daily_review.py).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API = "https://api.schwabapi.com"
TOKEN_URL = f"{API}/v1/oauth/token"
REFRESH_TOKEN_LIFETIME_S = 7 * 24 * 3600


class SchwabAuthError(RuntimeError):
    pass


class TokenExpired(SchwabAuthError):
    pass


def default_token_path() -> str:
    """SCHWAB_TOKEN_PATH, else data/schwab_token.json (the bind-mounted path
    on the droplet), else schwab-py's default on the Mac."""
    env = os.getenv("SCHWAB_TOKEN_PATH", "").strip()
    if env:
        return os.path.expanduser(env)
    if os.path.exists("data/schwab_token.json"):
        return "data/schwab_token.json"
    return os.path.expanduser("~/.config/schwab/token.json")


@dataclass
class SchwabRest:
    api_key: str
    app_secret: str
    token_path: str
    timeout_s: float = 20.0

    def __post_init__(self):
        self._session = requests.Session()
        self._doc: Dict = {}
        self._load()

    # ---- token ---------------------------------------------------------------
    def _load(self) -> None:
        with open(self.token_path) as fh:
            doc = json.load(fh)
        if "token" not in doc and "access_token" in doc:      # bare token dict
            doc = {"creation_timestamp": int(os.path.getmtime(self.token_path)), "token": doc}
        self._doc = doc

    @property
    def token(self) -> Dict:
        return self._doc.get("token", {})

    def refresh_token_age_days(self) -> float:
        created = float(self._doc.get("creation_timestamp") or 0)
        return (time.time() - created) / 86400.0 if created else 999.0

    def refresh_token_expired(self) -> bool:
        return self.refresh_token_age_days() * 86400.0 >= REFRESH_TOKEN_LIFETIME_S

    def access_token(self) -> str:
        tok = self.token
        if tok.get("access_token") and float(tok.get("expires_at", 0)) > time.time() + 60:
            return tok["access_token"]
        return self.refresh()

    def refresh(self) -> str:
        if self.refresh_token_expired():
            raise TokenExpired(
                f"Schwab refresh token is {self.refresh_token_age_days():.1f} days old "
                f"(limit 7): re-run `python -m trader.advisor.cli` on the Mac and copy "
                f"the new token to {self.token_path}")
        rt = self.token.get("refresh_token")
        if not rt:
            raise SchwabAuthError(f"no refresh_token in {self.token_path}")
        basic = base64.b64encode(f"{self.api_key}:{self.app_secret}".encode()).decode()
        r = self._session.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=self.timeout_s,
        )
        if r.status_code >= 400:
            body = r.text[:200].replace(rt, "***")
            if r.status_code in (400, 401):
                raise TokenExpired(f"Schwab token refresh rejected ({r.status_code}): {body}")
            raise SchwabAuthError(f"Schwab token refresh failed ({r.status_code}): {body}")
        new = r.json()
        tok = dict(self.token)
        tok.update(new)
        tok["expires_at"] = int(time.time() + float(new.get("expires_in", 1800)))
        tok.setdefault("refresh_token", rt)
        self._doc["token"] = tok
        try:
            tmp = self.token_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self._doc, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.token_path)
        except OSError as exc:
            logger.warning("schwab: could not persist refreshed token: %s", exc)
        return tok["access_token"]

    # ---- reads ---------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict] = None):
        r = self._session.get(f"{API}{path}", params=params,
                              headers={"Authorization": f"Bearer {self.access_token()}",
                                       "Accept": "application/json"},
                              timeout=self.timeout_s)
        if r.status_code == 401:
            raise SchwabAuthError("Schwab rejected the access token (401)")
        r.raise_for_status()
        return r.json()

    def account_hashes(self) -> List[Dict]:
        return self._get("/trader/v1/accounts/accountNumbers")

    def positions(self, account_index: int = 0) -> Dict:
        """-> {"positions": {sym: {qty, value, price}}, "cash": float,
               "total": float, "account": last-4}"""
        nums = self.account_hashes()
        if not nums:
            raise SchwabAuthError("Schwab returned no accounts")
        acct = nums[account_index]
        data = self._get(f"/trader/v1/accounts/{acct['hashValue']}", {"fields": "positions"})
        sa = data.get("securitiesAccount", {})
        out: Dict[str, dict] = {}
        for p in sa.get("positions", []):
            inst = p.get("instrument", {})
            sym = inst.get("symbol")
            qty = float(p.get("longQuantity", 0) or 0)
            val = float(p.get("marketValue", 0) or 0)
            if sym and qty > 0 and inst.get("assetType", "EQUITY") in ("EQUITY", "COLLECTIVE_INVESTMENT", "ETF"):
                out[sym] = {"qty": qty, "value": val, "price": (val / qty if qty else 0.0)}
        bal = sa.get("currentBalances", {})
        cash = float(bal.get("cashAvailableForTrading", bal.get("cashBalance", 0)) or 0)
        total = float(bal.get("liquidationValue", 0) or 0) or (sum(v["value"] for v in out.values()) + cash)
        return {"positions": out, "cash": cash, "total": total,
                "account": str(acct.get("accountNumber", ""))[-4:]}
