"""IRA advisor: research -> suggest -> review -> approve one at a time.

Lives inside the deployed package so the droplet can run the read-only daily
review (daily_review.py). Nothing here is ever placed automatically: orders go
to Schwab only through cli.py, in live mode, after a per-trade approval and a
typed ticker confirmation on the Mac.
"""
