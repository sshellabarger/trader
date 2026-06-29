"""
Universe screener coverage (liquidity + volatility pool builder).

  - ADV$ and ATR% are computed correctly from daily bars.
  - the screen applies the price/liquidity/volatility floors and ranks by ATR%.
  - build_pool prefilters by snapshot, fetches bars, scores, and screens.
  - write_pool / load_pool_symbols round-trip, and the sleeve's scan universe
    reads the pool file (STOCK_SLEEVE_POOL_FILE) with the right precedence.
  - broker.list_assets filters to tradable, plain US-equity tickers.
"""
import json

from trader.config import BrokerConfig, Config
from trader.broker import AlpacaBroker
from trader import universe_screen as us


def daily(c, a, v, o=None):
    """A daily bar with a symmetric high/low band of +/- a around close c (so
    true range = 2a when there's no gap) and volume v."""
    return {"o": o if o is not None else c, "h": c + a, "l": c - a, "c": c, "v": v}


def flat_series(price, a, v, n=16):
    """n identical daily bars: ATR = 2a, ATR% = 2a/price*100, ADV$ = price*v."""
    return [daily(price, a, v) for _ in range(n)]


# --------------------------------------------------------------------------
# Pure math
# --------------------------------------------------------------------------

def test_avg_dollar_volume():
    bars = flat_series(price=100.0, a=1.0, v=1_000_000)   # 100 * 1e6
    assert us.avg_dollar_volume(bars, window=20) == 100_000_000.0


def test_avg_dollar_volume_window_limits_to_recent():
    bars = [daily(10, 1, 100)] * 5 + [daily(10, 1, 1_000_000)] * 3   # window=3 -> only the big ones
    assert us.avg_dollar_volume(bars, window=3) == 10_000_000.0


def test_atr_pct_constant_range():
    bars = flat_series(price=100.0, a=1.0, v=1)            # TR=2 each, ATR=2, /100 -> 2%
    assert abs(us.atr_pct(bars, period=14) - 2.0) < 1e-9


def test_atr_pct_needs_history():
    assert us.atr_pct(flat_series(100, 1, 1, n=10), period=14) is None   # < period+1 bars


def test_score_symbol():
    sc = us.score_symbol("aaa", flat_series(100.0, 3.0, 1_000_000), window=20, atr_period=14)
    assert sc.symbol == "AAA"
    assert sc.price == 100.0
    assert sc.dollar_volume == 100_000_000.0
    assert abs(sc.atr_pct - 6.0) < 1e-9                    # 2*3/100*100


# --------------------------------------------------------------------------
# Screen (floors + ranking + top-N)
# --------------------------------------------------------------------------

def _score(sym, price, dv, atr):
    return us.SymbolScore(symbol=sym, price=price, dollar_volume=dv, atr_pct=atr)


def test_screen_floors_and_rank():
    crit = us.ScreenCriteria()   # price 5-1000, $vol>=20M, ATR%>=2.5, size 60
    scores = [
        _score("A", 50, 50e6, 5.0),     # passes
        _score("B", 200, 100e6, 3.0),   # passes
        _score("CHEAP", 2, 50e6, 8.0),  # fails price floor
        _score("THIN", 100, 5e6, 6.0),  # fails liquidity floor
        _score("CALM", 100, 30e6, 1.0), # fails volatility floor
    ]
    out = us.screen(scores, crit)
    assert [s.symbol for s in out] == ["A", "B"]           # ranked by ATR% desc


def test_screen_top_n():
    crit = us.ScreenCriteria(size=1)
    scores = [_score("A", 50, 50e6, 5.0), _score("B", 50, 50e6, 3.0)]
    assert [s.symbol for s in us.screen(scores, crit)] == ["A"]


# --------------------------------------------------------------------------
# build_pool end-to-end with a fake broker
# --------------------------------------------------------------------------

class FakeBroker:
    def __init__(self, snapshots, bars):
        self.snapshots = snapshots
        self.bars = bars

    def get_snapshots(self, symbols):
        return {s: self.snapshots[s] for s in symbols if s in self.snapshots}

    def get_bars(self, symbol, **kw):
        return list(self.bars.get(symbol, []))


def _snap(price, vol):
    return {"latestTrade": {"p": price}, "dailyBar": {"c": price, "v": vol}}


def test_build_pool_prefilters_scores_and_screens():
    snapshots = {
        "AAA": _snap(100, 1_000_000),   # survives prefilter
        "BBB": _snap(50, 1_000_000),    # survives prefilter
        "CHEAP": _snap(2, 1_000_000),   # dropped: price < $5
        "THIN": _snap(100, 1_000_000),  # survives prefilter, fails ADV at screen
    }
    bars = {
        "AAA": flat_series(100.0, 3.0, 1_000_000),   # ATR% 6, ADV $100M
        "BBB": flat_series(50.0, 0.75, 1_000_000),   # ATR% 3, ADV $50M
        "THIN": flat_series(100.0, 2.0, 50_000),     # ATR% 4 but ADV $5M -> cut
        # CHEAP never fetched (prefiltered out)
    }
    broker = FakeBroker(snapshots, bars)
    pool = us.build_pool(broker, list(snapshots), us.ScreenCriteria())
    assert [s.symbol for s in pool] == ["AAA", "BBB"]      # THIN cut on liquidity, CHEAP on price


# --------------------------------------------------------------------------
# write / load round-trip + sleeve precedence
# --------------------------------------------------------------------------

def test_write_and_load_pool(tmp_path):
    pool = [_score("NVDA", 120, 9e9, 4.0), _score("PLTR", 30, 2e9, 6.0)]
    path = tmp_path / "pool.json"
    us.write_pool(str(path), pool, us.ScreenCriteria())
    payload = json.loads(path.read_text())
    assert payload["symbols"] == ["NVDA", "PLTR"]
    assert payload["criteria"]["min_atr_pct"] == 2.5
    assert us.load_pool_symbols(str(path)) == ["NVDA", "PLTR"]


def test_load_pool_missing_returns_empty():
    assert us.load_pool_symbols("/no/such/pool.json") == []


def test_sleeve_uses_pool_file(tmp_path):
    path = tmp_path / "pool.json"
    us.write_pool(str(path), [_score("ABC", 10, 5e7, 5.0), _score("XYZ", 20, 5e7, 4.0)],
                  us.ScreenCriteria())
    c = Config()
    c.strategy.stock_sleeve_symbols = ""              # no explicit override
    c.strategy.stock_sleeve_pool_file = str(path)
    assert c.stock_sleeve_scan_universe() == ["ABC", "XYZ"]


def test_explicit_symbols_beat_pool_file(tmp_path):
    path = tmp_path / "pool.json"
    us.write_pool(str(path), [_score("ABC", 10, 5e7, 5.0)], us.ScreenCriteria())
    c = Config()
    c.strategy.stock_sleeve_symbols = "NVDA,AMD"
    c.strategy.stock_sleeve_pool_file = str(path)
    assert c.stock_sleeve_scan_universe() == ["NVDA", "AMD"]   # explicit wins


def test_missing_pool_file_falls_back_to_categories():
    c = Config()
    c.strategy.stock_sleeve_symbols = ""
    c.strategy.stock_sleeve_pool_file = "/no/such/pool.json"
    c.strategy.stock_sleeve_universe = "tech_volatile"
    uni = c.stock_sleeve_scan_universe()
    assert "PLTR" in uni                                # fell back to the static category


# --------------------------------------------------------------------------
# broker.list_assets parsing
# --------------------------------------------------------------------------

def test_list_assets_filters(monkeypatch):
    broker = AlpacaBroker(BrokerConfig())
    assets = [
        {"symbol": "AAPL", "tradable": True, "exchange": "NASDAQ"},
        {"symbol": "OTCX", "tradable": True, "exchange": "OTC"},       # OTC dropped
        {"symbol": "NOPE", "tradable": False, "exchange": "NYSE"},     # not tradable
        {"symbol": "BRK.B", "tradable": True, "exchange": "NYSE"},     # dot dropped
        {"symbol": "TOOLONG", "tradable": True, "exchange": "NYSE"},   # >5 chars dropped
        {"symbol": "AMD", "tradable": True, "exchange": "NASDAQ"},
    ]
    monkeypatch.setattr(broker, "_request", lambda *a, **k: assets)
    assert broker.list_assets() == ["AAPL", "AMD"]


def test_list_assets_empty_on_bad_response(monkeypatch):
    broker = AlpacaBroker(BrokerConfig())
    monkeypatch.setattr(broker, "_request", lambda *a, **k: None)
    assert broker.list_assets() == []
