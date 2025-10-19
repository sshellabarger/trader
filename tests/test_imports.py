def test_import():
    import trading_bot
    from trading_bot.engine import Trader
    from trading_bot.webapp import app
    assert callable(Trader)
