"""
CLI — ETF-focused day trading.

Usage:
  python -m trader run              # paper trade TQQQ
  python -m trader run --dry-run    # signals only
  python -m trader backtest --start 2025-01-02 --end 2025-06-30
  python -m trader backtest --start 2025-01-02 --end 2025-06-30 --symbol TQQQ
  python -m trader walkforward --start 2025-01-02 --end 2025-06-30 --symbol TQQQ
  python -m trader kalshi-record                # 24/7 prediction-market recorder
  python -m trader kalshi-discover --category "Climate and Weather"
  python -m trader kalshi-weather               # weather fair values vs market
  python -m trader kalshi-sports-scan           # Pinnacle devig vs Kalshi sports
  python -m trader kalshi-weather-backtest --inputs data/kalshi_wk2/weather_backtest_inputs.csv
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .engine import Engine
from .backtest import Backtester
from .walkforward import WalkForwardValidator

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO", log_file: str = ""):
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


def cmd_run(args):
    config = Config()
    config.dry_run = args.dry_run
    config.log_level = args.log_level
    if args.symbol:
        config.strategy.primary_symbol = args.symbol
        config.strategy.use_leveraged = False
    setup_logging(config.log_level, config.log_file)

    engine = Engine(config)
    engine.run(loop_interval=args.interval)


def cmd_backtest(args):
    config = Config()
    config.log_level = args.log_level
    # Optional overnight experiments (default off = baseline behavior).
    if args.overnight_filter:
        config.strategy.orb_require_overnight_alignment = True
    if args.overnight_gap_min is not None:
        config.strategy.orb_overnight_gap_min_pct = args.overnight_gap_min
    if args.hold_overnight:
        config.strategy.orb_hold_overnight = True
    setup_logging(config.log_level)

    # Default to the exact instruments the live engine trades (e.g. TQQQ+SQQQ
    # under the leveraged default), so the backtest models live behavior.
    symbols = [args.symbol] if args.symbol else config.get_trading_symbols()
    bt = Backtester(config)
    result = bt.run(symbols, start_date=args.start, end_date=args.end)

    print(f"\n{'='*50}")
    print(f"BACKTEST: {'+'.join(symbols)} {args.start} -> {args.end}")
    print(f"  Overnight filter: {'ON' if config.strategy.orb_require_overnight_alignment else 'off'}"
          f"   |   Hold overnight: {'ON' if config.strategy.orb_hold_overnight else 'off'}")
    print(f"  Return: {result.total_return_pct:+.2f}%  (${result.total_pnl:+,.2f})")
    print(f"  Capital: ${result.initial_capital:,.0f} -> ${result.final_capital:,.2f}")
    print(f"  Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%")
    print(f"  Avg win: ${result.avg_win:,.2f}  Avg loss: ${result.avg_loss:,.2f}")
    print(f"  Profit factor: {result.profit_factor:.2f}  Sharpe: {result.sharpe_ratio:.2f}")
    print(f"  Max drawdown: {result.max_drawdown_pct:.2f}%")
    for strat, stats in result.by_strategy.items():
        print(f"  [{strat}] {stats['trades']} trades, {stats['win_rate']:.0f}% win, ${stats['pnl']:+,.2f}")


def cmd_sleeve_backtest(args):
    """Replay the scanner-driven stock sleeve over history (see
    sleeve_backtest.py). Decision-grade runs should set
    BACKTEST_ENTRY_FILL_NEXT_OPEN=true and BACKTEST_SLIPPAGE_BPS=10."""
    from .sleeve_backtest import SleeveBacktester, run_sweep

    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    st = config.strategy
    if args.band:
        try:
            lo, hi = (float(x) for x in args.band.split(","))
        except ValueError:
            print(f"--band expects 'lo,hi' (e.g. 0.5,1.2), got {args.band!r}")
            return
        st.orb_min_range_pct, st.orb_max_range_pct = lo, hi
    if args.atr_band:
        st.orb_range_band_atr = True
    if args.atr_lo is not None:
        st.orb_range_atr_lo = args.atr_lo
    if args.atr_hi is not None:
        st.orb_range_atr_hi = args.atr_hi
    if args.entry_window is not None:
        st.orb_entry_window_minutes = args.entry_window
    if args.candidates is not None:
        # Both trims, mirroring the live two-stage behavior (scanner trims to
        # ScannerConfig.max_candidates BEFORE the sleeve's top-N).
        config.scanner.max_candidates = args.candidates
        st.stock_sleeve_max_candidates = args.candidates

    if not (config.backtest.entry_fill_next_open
            and config.backtest.slippage_bps >= 10):
        print("NOTE: honesty knobs not fully on "
              f"(next_open_fill={config.backtest.entry_fill_next_open}, "
              f"slippage={config.backtest.slippage_bps}bps). Fine for exploring; "
              "decision-grade runs want BACKTEST_ENTRY_FILL_NEXT_OPEN=true "
              "BACKTEST_SLIPPAGE_BPS=10.")

    bt = SleeveBacktester(config, cache_dir=args.cache_dir,
                          rvol_mode=args.rvol, offline=args.offline)

    if args.calibrate:
        bt.calibrate(args.calibrate)
        return

    if args.fetch or args.fetch_only:
        bt.prefetch(args.start, args.end)
        if args.fetch_only:
            return

    if args.sweep:
        run_sweep(args.start, args.end, args.train_end, args.cache_dir,
                  args.rvol, args.offline, label=args.sleeve_label)
        return

    result = bt.run_sleeve(args.start, args.end, label=args.sleeve_label)
    print(f"\n{'='*50}")
    print(f"SLEEVE REPLAY: {args.start} -> {args.end}")
    print(f"  Sessions: {len(bt.day_log)}  with picks: "
          f"{sum(1 for d in bt.day_log if d['picks'])}")
    print(f"  Return: {result.total_return_pct:+.2f}%  (${result.total_pnl:+,.2f})")
    print(f"  Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%  "
          f"Trades/day: {result.trades_per_day}")
    print(f"  Profit factor: {result.profit_factor:.2f}  Sharpe: {result.sharpe_ratio:.2f}")
    print(f"  Max drawdown: {result.max_drawdown_pct:.2f}%")


def cmd_walkforward(args):
    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    symbols = [args.symbol] if args.symbol else config.get_trading_symbols()

    wf = WalkForwardValidator(
        config,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        label=getattr(args, "label", ""),
    )
    result = wf.run(symbols, start_date=args.start, end_date=args.end)

    print(f"\n{'='*60}")
    print(f"WALK-FORWARD: {'+'.join(symbols)}")
    print(f"{'='*60}")
    print(f"  {'':24s} {'TRAIN':>10s} {'TEST':>10s}")
    print(f"  {'P&L':24s} ${result.total_train_pnl:>+9,.2f} ${result.total_test_pnl:>+9,.2f}")
    print(f"  {'Trades':24s} {result.total_train_trades:>10d} {result.total_test_trades:>10d}")
    print(f"  {'Win rate':24s} {result.train_win_rate:>9.1f}% {result.test_win_rate:>9.1f}%")
    print(f"  {'Profit factor':24s} {result.train_profit_factor:>10.2f} {result.test_profit_factor:>10.2f}")
    print()
    if result.consistent:
        print("  VERDICT: CONSISTENT")
    elif result.total_train_pnl > 0 and result.total_test_pnl <= 0:
        print("  VERDICT: OVERFITTING")
    elif result.total_train_pnl <= 0:
        print("  VERDICT: NO EDGE")
    else:
        print("  VERDICT: MIXED")


def cmd_screen(args):
    """Build the stock-sleeve pool by liquidity + volatility and write it to a
    file the sleeve reads (STOCK_SLEEVE_POOL_FILE). Meant to run weekly."""
    from .broker import AlpacaBroker
    from . import universe as uni
    from . import universe_screen as us

    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    broker = AlpacaBroker(config.broker)
    if args.seed_universe:
        seed, src = uni.get_universe(), "in-repo universe"
    else:
        seed, src = broker.list_assets(), "Alpaca active US equities"
        if not seed:
            print("No assets from Alpaca (check keys/network); using the in-repo universe.")
            seed, src = uni.get_universe(), "in-repo universe (fallback)"
    if args.limit_seed:
        seed = seed[: args.limit_seed]

    crit = us.ScreenCriteria(
        min_price=args.min_price, max_price=args.max_price,
        min_dollar_volume=args.min_dollar_vol, min_atr_pct=args.min_atr_pct,
        window=args.window, atr_period=args.atr_period, size=args.max,
    )
    print(f"Screening {len(seed)} symbols ({src}) -> pool of {crit.size}  "
          f"[price ${crit.min_price:g}-${crit.max_price:g}, "
          f">=${crit.min_dollar_volume/1e6:g}M/day, ATR% >= {crit.min_atr_pct:g}]")
    pool = us.build_pool(broker, seed, crit, prefilter=not args.no_prefilter)
    us.write_pool(args.out, pool, crit)

    print(f"\nWrote {len(pool)} names to {args.out}:")
    print(f"  {'SYM':6s} {'PRICE':>9s} {'$VOL/DAY':>11s} {'ATR%':>7s}")
    for s in pool[:30]:
        print(f"  {s.symbol:6s} {s.price:>9.2f} {s.dollar_volume/1e6:>9.1f}M {s.atr_pct:>6.1f}%")
    if len(pool) > 30:
        print(f"  ... +{len(pool) - 30} more")
    if not pool:
        print("  (empty — loosen the floors, check the data feed, or widen the price band)")


def cmd_kalshi_record(args):
    """24/7 Kalshi snapshot recorder (phase 0 of the prediction-market sleeve;
    public data only, no credentials, no orders). See trader/kalshi/."""
    from .kalshi.client import KalshiClient
    from .kalshi.config import KalshiConfig
    from .kalshi.recorder import KalshiRecorder

    setup_logging(args.log_level)
    kcfg = KalshiConfig()
    if args.series:
        kcfg.series = args.series
    recorder = KalshiRecorder(KalshiClient(kcfg), kcfg)
    if args.settlements_only:
        wrote = recorder.sweep_settlements()
        print(f"Settlement sweep complete: {wrote} new outcomes")
        return
    recorder.run_forever()


def cmd_kalshi_sports_scan(args):
    """Devigged Pinnacle fair vs live Kalshi sports quotes. One Odds-API
    credit per series scanned; every row is appended to the forward-sample
    log for later settlement joins (measurement only, no orders)."""
    import os
    from datetime import datetime, timezone
    from .kalshi.client import KalshiClient, price_cents
    from .kalshi.config import KalshiConfig
    from .kalshi import sports as sp

    setup_logging(args.log_level)
    key = os.getenv("ODDS_API_KEY", "")
    if not key:
        env_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "trader", ".env")
        if not os.path.exists(env_path):
            env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if line.strip().startswith("ODDS_API_KEY="):
                    key = line.strip().split("=", 1)[1]
    if not key:
        print("ODDS_API_KEY not set (env or trader/.env)")
        return

    kcfg = KalshiConfig()
    client = KalshiClient(kcfg)
    series_list = ([s.strip().upper() for s in args.series.split(",") if s.strip()]
                   if args.series else ["KXMLBGAME", "KXNFLGAME"])
    now = datetime.now(timezone.utc)
    log_rows = []
    for series in series_list:
        if series not in sp.SPORT_KEYS:
            print(f"{series}: not a sports series")
            continue
        markets = client.get_markets(series_ticker=series, status="open")
        by_event = {}
        for m in markets:
            by_event.setdefault(m.get("event_ticker", ""), []).append(m)
        try:
            games = sp.fetch_odds(series, key)
        except Exception as exc:  # noqa: BLE001
            print(f"{series}: odds fetch failed: {exc}")
            continue
        matched, unmatched = sp.match_games_to_events(
            series, games, list(by_event))
        print(f"\n{series}: {len(games)} games from Pinnacle, "
              f"{len(matched)} matched to Kalshi events")
        rows = []
        for event, game in matched.items():
            start = datetime.fromisoformat(
                game["commence_time"].replace("Z", "+00:00"))
            if (start - now).total_seconds() > args.hours * 3600:
                continue
            fair_by_name = sp.fair_probs_from_game(game)
            if not fair_by_name:
                continue
            code_of = {name: sp.CODES[series].get(name, "")
                       for name in fair_by_name}
            for m in by_event[event]:
                leaf = m.get("ticker", "").rsplit("-", 1)[-1]
                name = next((n for n, c in code_of.items() if c == leaf), None)
                yb, ya = price_cents(m, "yes_bid"), price_cents(m, "yes_ask")
                if name is None or yb is None or ya is None \
                        or not (0 < yb <= ya < 100):
                    continue
                out = sp.evaluate(100.0 * fair_by_name[name], yb, ya)
                out.update({"t": now.isoformat(timespec="seconds"),
                            "series": series, "ticker": m["ticker"],
                            "event": event, "book": "pinnacle",
                            "commence": game["commence_time"]})
                rows.append(out)
        rows.sort(key=lambda r: -r["net_edge"])
        print(f"  {'ticker':<32}{'fair':>6}{'bid':>5}{'ask':>5}{'edge':>7}  side")
        for r in rows:
            flag = "  <-- ENTER" if r["net_edge"] >= args.min_edge else ""
            print(f"  {r['ticker']:<32}{r['fair']:>6}{r['bid']:>5}"
                  f"{r['ask']:>5}{r['net_edge']:>7}  {r['side']}{flag}")
        if unmatched:
            print("  unmatched:", "; ".join(unmatched[:6]))
        log_rows.extend(rows)
    if log_rows and not args.no_log:
        import os as _os
        path = _os.path.join(kcfg.data_dir, "sports_scans.jsonl")
        sp.append_scan_log(path, log_rows)
        print(f"\nlogged {len(log_rows)} rows -> {path}")


def cmd_kalshi_weather(args):
    """Fair values + net edges for open KXHIGH* markets from the GEFS+ECMWF
    ensembles at the official settlement stations (measurement only)."""
    from .kalshi.client import KalshiClient
    from .kalshi.config import KalshiConfig
    from .kalshi import weather as wx

    setup_logging(args.log_level)
    client = KalshiClient(KalshiConfig())
    cfg = wx.WeatherConfig()
    series_list = ([s.strip().upper() for s in args.series.split(",") if s.strip()]
                   if args.series else list(wx.STATIONS))
    pools_cache = {}
    for series in series_list:
        if series not in wx.STATIONS:
            print(f"{series}: no station mapping, skipping")
            continue
        markets = client.get_markets(series_ticker=series, status="open")
        if not markets:
            print(f"{series}: no open markets")
            continue
        by_event = {}
        for m in markets:
            by_event.setdefault(m.get("event_ticker", ""), []).append(m)
        for event in sorted(by_event):
            date = wx.parse_event_date(event)
            if not date or (args.date and date != args.date):
                continue
            key = (series, date)
            if key not in pools_cache:
                try:
                    raw = wx.fetch_ensemble_daymax(series, date)
                except Exception as exc:  # noqa: BLE001
                    print(f"{series} {date}: ensemble fetch failed: {exc}")
                    pools_cache[key] = None
                    continue
                pools_cache[key] = wx.corrected_pools(
                    raw, cfg.bias.get(series, 0.0), cfg.spread.get(series, 1.0))
            pools = pools_cache[key]
            if not pools:
                continue
            n_members = sum(len(p) for p in pools.values())
            print(f"\n{event}  ({wx.STATIONS[series].name}, {n_members} members)")
            print(f"  {'ticker':<28}{'fair':>6}{'bid':>5}{'ask':>5}{'edge':>7}  side")
            for m in sorted(by_event[event], key=lambda x: x.get("ticker", "")):
                spec = wx.spec_from_api_market(m)
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                from .kalshi.client import price_cents
                yb, ya = price_cents(m, "yes_bid"), price_cents(m, "yes_ask")
                if spec is None or yb is None or ya is None or not (0 < yb <= ya < 100):
                    continue
                fair = wx.fair_value_cents(spec, pools, cfg)
                out = wx.evaluate(spec, fair, yb, ya)
                flag = "  <-- ENTER" if out["net_edge"] >= args.min_edge else ""
                print(f"  {out['ticker']:<28}{out['fair']:>6}{out['bid']:>5}"
                      f"{out['ask']:>5}{out['net_edge']:>7}  {out['side']}{flag}")


def cmd_kalshi_weather_backtest(args):
    """Score the weather model vs the recorded market week (Brier + paper
    trades). See kalshi/weather_backtest.py for the honesty caveats."""
    from .kalshi import weather_backtest as wb

    setup_logging(args.log_level)
    result = wb.run(args.inputs, cache_dir=args.cache_dir,
                    min_edge=args.min_edge)
    wb.report(result)


def cmd_kalshi_discover(args):
    """List current Kalshi series so KALSHI_SERIES can be set without guessing
    tickers (naming drifts: HIGHCHI died, KXHIGHCHI is live)."""
    from .kalshi.client import KalshiClient
    from .kalshi.config import KalshiConfig

    setup_logging(args.log_level)
    client = KalshiClient(KalshiConfig())
    categories = ([args.category] if args.category else
                  ["Climate and Weather", "Economics", "Sports", "Financials"])
    for cat in categories:
        series = client.get_series_list(category=cat)
        print(f"\n{cat}: {len(series)} series")
        for s in series:
            print(f"  {s.get('ticker', '?'):28s} {s.get('title', '')[:70]}")


def main():
    parser = argparse.ArgumentParser(description="DayTrader v2 — ETF Edition")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Start trading")
    run_p.add_argument("--dry-run", action="store_true", help="Signal-only mode")
    run_p.add_argument("--interval", type=int, default=30, help="Loop interval seconds")
    run_p.add_argument("--symbol", type=str, default="", help="Override primary symbol")

    bt_p = sub.add_parser("backtest", help="Run backtest")
    bt_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    bt_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    bt_p.add_argument("--symbol", type=str, default="", help="Symbol to test")
    bt_p.add_argument("--overnight-filter", action="store_true",
                      help="enable the ORB overnight-alignment entry gate")
    bt_p.add_argument("--overnight-gap-min", type=float, default=None,
                      help="min overnight gap %% for the filter (default 0.0)")
    bt_p.add_argument("--hold-overnight", action="store_true",
                      help="carry winning ORB positions overnight, exit next open")

    wf_p = sub.add_parser("walkforward", help="Walk-forward validation")
    wf_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    wf_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    wf_p.add_argument("--symbol", type=str, default="", help="Symbol")
    wf_p.add_argument("--train-days", type=int, default=40)
    wf_p.add_argument("--test-days", type=int, default=20)
    wf_p.add_argument("--step-days", type=int, default=20)
    wf_p.add_argument("--label", default="", help=(
        "Scenario tag for the summary file (walkforward_summary_<label>.json) "
        "so runs stop overwriting each other"))

    sb_p = sub.add_parser("sleeve-backtest",
                          help="Replay the scanner-driven stock sleeve on history")
    sb_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    sb_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    sb_p.add_argument("--fetch", action="store_true",
                      help="prefetch daily + premarket caches before replaying")
    sb_p.add_argument("--fetch-only", action="store_true",
                      help="prefetch caches and exit (no replay)")
    sb_p.add_argument("--offline", action="store_true",
                      help="never touch the network; cache misses are skipped+counted")
    sb_p.add_argument("--rvol", choices=["premarket", "off"], default="premarket",
                      help="scanner ranking weight (premarket = |gap| x premarket-volume "
                           "ratio from 4:00-9:30 IEX bars, gap as tiebreaker; off = |gap| "
                           "only). Never a floor — IEX premarket can't reproduce the live "
                           "0.5 filter (measured max ratio 0.095)")
    sb_p.add_argument("--cache-dir", default="data/sleeve_cache")
    sb_p.add_argument("--band", default="",
                      help="fixed range band as 'lo,hi' in %% of price (hi=0 uncaps)")
    sb_p.add_argument("--atr-band", action="store_true",
                      help="use the ATR-scaled range band instead of the fixed %% band")
    sb_p.add_argument("--atr-lo", type=float, default=None,
                      help="ATR band lower multiple (default 0.15)")
    sb_p.add_argument("--atr-hi", type=float, default=None,
                      help="ATR band upper multiple (default 0.45)")
    sb_p.add_argument("--entry-window", type=int, default=None,
                      help="ORB entry window minutes (live default 3)")
    sb_p.add_argument("--candidates", type=int, default=None,
                      help="picks per day (sets scanner AND sleeve trims)")
    sb_p.add_argument("--sweep", action="store_true",
                      help="run the band x entry-window grid with a train/test split")
    sb_p.add_argument("--train-end", default=None,
                      help="last day of the sweep's train segment (default: 70%% split)")
    sb_p.add_argument("--calibrate", default="",
                      help="compare sim picks vs live journal summaries in this dir "
                           "(e.g. trade_logs), then exit")
    sb_p.add_argument("--sleeve-label", default="",
                      help="tag for output filenames in backtest_results/")

    sc_p = sub.add_parser("screen-universe",
                          help="Build the stock-sleeve pool by liquidity + volatility")
    sc_p.add_argument("--out", default="data/pool.json", help="output pool file")
    sc_p.add_argument("--max", type=int, default=60, help="pool size (top-N by ATR%%)")
    sc_p.add_argument("--min-dollar-vol", type=float, default=20_000_000.0,
                      help="min 20-day average $ volume")
    sc_p.add_argument("--min-atr-pct", type=float, default=2.5, help="min ATR%% of price")
    sc_p.add_argument("--min-price", type=float, default=5.0)
    sc_p.add_argument("--max-price", type=float, default=1000.0)
    sc_p.add_argument("--window", type=int, default=20, help="days for avg $ volume")
    sc_p.add_argument("--atr-period", type=int, default=14)
    sc_p.add_argument("--seed-universe", action="store_true",
                      help="seed from the in-repo universe instead of all Alpaca assets")
    sc_p.add_argument("--limit-seed", type=int, default=0, help="cap seed size (debug)")
    sc_p.add_argument("--no-prefilter", action="store_true",
                      help="skip the snapshot pre-filter")

    kr_p = sub.add_parser("kalshi-record",
                          help="24/7 Kalshi market recorder (phase 0, no orders)")
    kr_p.add_argument("--series", default="",
                      help="comma-separated series tickers (default: "
                           "KALSHI_SERIES or the built-in verified set)")
    kr_p.add_argument("--settlements-only", action="store_true",
                      help="run one settlement sweep and exit")

    kw_p = sub.add_parser("kalshi-weather",
                          help="weather fair values vs market (no orders)")
    kw_p.add_argument("--series", default="", help="comma-separated KXHIGH* subset")
    kw_p.add_argument("--date", default="", help="only this event date YYYY-MM-DD")
    kw_p.add_argument("--min-edge", type=float, default=3.0,
                      help="flag threshold in cents (phase-0 paper rule: 3)")

    ks_p = sub.add_parser("kalshi-sports-scan",
                          help="Pinnacle devig vs Kalshi sports quotes (no orders)")
    ks_p.add_argument("--series", default="", help="KXMLBGAME,KXNFLGAME subset")
    ks_p.add_argument("--hours", type=float, default=36.0,
                      help="only games starting within this many hours")
    ks_p.add_argument("--min-edge", type=float, default=2.0,
                      help="flag threshold cents (phase-0 sports rule: 2)")
    ks_p.add_argument("--no-log", action="store_true",
                      help="skip appending to the forward-sample log")

    kwa_p = sub.add_parser("kalshi-weather-archive",
                           help="cache today+tomorrow ensemble day-max pools "
                                "(run daily; the API keeps members ~4-5 days)")
    kwa_p.add_argument("--cache-dir", default="data/wx_cache")

    kwb_p = sub.add_parser("kalshi-weather-backtest",
                           help="score weather model vs recorded market week")
    kwb_p.add_argument("--inputs", required=True,
                       help="CSV from the banked recordings (ticker,outcome,quotes)")
    kwb_p.add_argument("--cache-dir", default="data/wx_cache",
                       help="archived-ensemble cache directory")
    kwb_p.add_argument("--min-edge", type=float, default=3.0)

    kd_p = sub.add_parser("kalshi-discover",
                          help="list Kalshi series tickers by category")
    kd_p.add_argument("--category", default="",
                      help='e.g. "Climate and Weather", "Economics", "Sports"')

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "walkforward":
        cmd_walkforward(args)
    elif args.command == "sleeve-backtest":
        cmd_sleeve_backtest(args)
    elif args.command == "screen-universe":
        cmd_screen(args)
    elif args.command == "kalshi-record":
        cmd_kalshi_record(args)
    elif args.command == "kalshi-sports-scan":
        cmd_kalshi_sports_scan(args)
    elif args.command == "kalshi-weather":
        cmd_kalshi_weather(args)
    elif args.command == "kalshi-weather-archive":
        from .kalshi import weather_backtest as _wb
        setup_logging(args.log_level)
        _wb.archive_today(args.cache_dir)
    elif args.command == "kalshi-weather-backtest":
        cmd_kalshi_weather_backtest(args)
    elif args.command == "kalshi-discover":
        cmd_kalshi_discover(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
