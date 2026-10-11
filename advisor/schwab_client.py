"""Guarded schwab-py wrapper.

The only write method is place_market_order, which places ONE order. Everything
else is read-only. `schwab` is imported lazily inside the methods so this module
(and the engine/tests) import fine even when schwab-py isn't installed.

Setup once:
  pip install schwab-py
  Create an app at https://developer.schwab.com (callback https://127.0.0.1:8182),
  then the first connect() opens a browser to mint the token file.
"""
from __future__ import annotations

from typing import Dict

from .engine import Trade


class SchwabClient:
    def __init__(self, client, account_hash):
        self._c = client
        self._hash = account_hash

    @classmethod
    def connect(cls, api_key, app_secret, callback_url, token_path, account_index=0):
        from schwab.auth import easy_client
        c = easy_client(api_key=api_key, app_secret=app_secret,
                        callback_url=callback_url, token_path=token_path)
        nums = c.get_account_numbers().json()
        return cls(c, nums[account_index]["hashValue"])

    # ---- read only ----
    def get_positions(self) -> Dict[str, dict]:
        from schwab.client import Client
        data = self._c.get_account(self._hash, fields=Client.Account.Fields.POSITIONS).json()
        out = {}
        for p in data.get("securitiesAccount", {}).get("positions", []):
            sym = p.get("instrument", {}).get("symbol")
            qty = float(p.get("longQuantity", 0) or 0)
            val = float(p.get("marketValue", 0) or 0)
            if sym and qty > 0:
                out[sym] = {"qty": qty, "value": val, "price": val / qty if qty else 0.0}
        return out

    def get_cash(self) -> float:
        from schwab.client import Client
        bal = self._c.get_account(self._hash).json().get("securitiesAccount", {}).get("currentBalances", {})
        return float(bal.get("cashAvailableForTrading", bal.get("cashBalance", 0)) or 0)

    def get_quote(self, symbol) -> float:
        q = self._c.get_quote(symbol).json().get(symbol, {}).get("quote", {})
        return float(q.get("lastPrice") or q.get("mark") or 0)

    def get_daily_history(self, symbol, start=None, end=None):
        """Daily (split/dividend-adjusted) candles as [{date,open,high,low,close,volume}]."""
        import datetime as _dt
        kw = {}
        if start:
            kw["start_datetime"] = _dt.datetime.strptime(start, "%Y-%m-%d")
        if end:
            kw["end_datetime"] = _dt.datetime.strptime(end, "%Y-%m-%d")
        data = self._c.get_price_history_every_day(symbol, **kw).json()
        out = []
        for c in data.get("candles", []):
            d = _dt.datetime.utcfromtimestamp(c["datetime"] / 1000).strftime("%Y-%m-%d")
            out.append({"date": d, "open": c.get("open"), "high": c.get("high"),
                        "low": c.get("low"), "close": c.get("close"), "volume": c.get("volume")})
        return out

    def order_status(self, order_id) -> str:
        return self._c.get_order(order_id, self._hash).json().get("status", "UNKNOWN")

    # ---- the one write ----
    def place_market_order(self, trade: Trade) -> str:
        from schwab.orders.equities import equity_buy_market, equity_sell_market
        from schwab.utils import Utils
        builder = (equity_buy_market(trade.symbol, trade.quantity) if trade.side == "BUY"
                   else equity_sell_market(trade.symbol, trade.quantity))
        resp = self._c.place_order(self._hash, builder.build())
        return Utils(self._c, self._hash).extract_order_id(resp)
