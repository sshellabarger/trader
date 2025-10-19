def test_imports():
    import trading_bot
    from trading_bot.engine import Trader
    from trading_bot.webapp import app
    assert callable(Trader)
    assert app is not None
