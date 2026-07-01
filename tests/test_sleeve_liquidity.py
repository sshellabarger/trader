"""
Sleeve pool restricted to IEX-tradable names (July 2026 fix).

On the free IEX feed the live sleeve got no intraday bars for thin / low-priced
names (e.g. BITF), so it never traded. This guards the interim fix until the
paid SIP feed is enabled:
  - a curated "liquid_movers" category exists and excludes the thin names;
  - it is the DEFAULT sleeve universe;
  - the weekly screener's price and dollar-volume floors reject IEX-thin names.
"""
from trader import universe
from trader import universe_screen as us
from trader.config import Config


# Low-priced / low-float names IEX barely prints intraday bars for.
THIN = {"BITF", "SOUN", "BBAI", "CIFR", "RIOT", "CLSK", "QUBT", "RGTI", "IONQ"}


def test_liquid_movers_exists_and_excludes_thin_names():
    names = universe.UNIVERSE["liquid_movers"]
    assert len(names) >= 15
    assert all(n.isupper() and n.replace(".", "").isalpha() for n in names)
    assert THIN.isdisjoint(names)
    assert {"NVDA", "AMD", "TSLA", "COIN"} <= set(names)


def test_get_universe_resolves_liquid_movers():
    got = universe.get_universe(["liquid_movers"])
    assert got == sorted(set(universe.UNIVERSE["liquid_movers"]))
    assert "BITF" not in got


def test_liquid_movers_is_default_sleeve_universe(monkeypatch):
    for k in ("STOCK_SLEEVE_UNIVERSE", "STOCK_SLEEVE_SYMBOLS", "STOCK_SLEEVE_POOL_FILE"):
        monkeypatch.delenv(k, raising=False)
    c = Config()
    assert c.strategy.stock_sleeve_universe == "liquid_movers"
    scan = c.stock_sleeve_scan_universe()
    assert "NVDA" in scan
    assert "BITF" not in scan


def test_screen_defaults_reject_iex_thin_names():
    crit = us.ScreenCriteria()
    assert crit.min_price >= 10.0
    assert crit.min_dollar_volume >= 50_000_000.0
    scores = [
        us.SymbolScore("GOOD", price=50.0, dollar_volume=80e6, atr_pct=4.0),
        us.SymbolScore("CHEAP", price=3.0, dollar_volume=80e6, atr_pct=9.0),   # price floor
        us.SymbolScore("THIN", price=50.0, dollar_volume=25e6, atr_pct=9.0),   # liquidity floor
    ]
    out = [s.symbol for s in us.screen(scores, crit)]
    assert out == ["GOOD"]
