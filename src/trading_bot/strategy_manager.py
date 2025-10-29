"""
Enhanced Strategy Manager with Regime Detection
Includes: momentum, mean_reversion, news, volume, earnings, longterm_trend, longterm_momentum, crypto, forex, etf
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import statistics

# Import strategy scoring functions
from . import strategies as strat
from .strategy_configs import get_strategy_config


class MarketRegime(Enum):
    """Market regime classification"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


@dataclass
class SignalResult:
    """Individual strategy signal result"""
    strategy_name: str
    score: float
    confidence: float
    regime_match: bool
    details: Dict


@dataclass
class CombinedSignal:
    """Combined signal from multiple strategies"""
    symbol: str
    final_score: float
    confidence: float
    active_strategies: List[str]
    regime: MarketRegime
    signals: List[SignalResult]
    metadata: Dict
    is_crypto: bool = False


class RegimeDetector:
    """Detect market regime for a symbol"""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def detect_regime(
        self,
        current_price: float,
        open_price: float,
        prev_close: float,
        high: float,
        low: float,
        volume: Optional[float] = None,
        historical_volatility: Optional[float] = None
    ) -> Tuple[MarketRegime, Dict]:
        """Detect market regime based on price action"""
        details = {
            'price_range_pct': 0,
            'intraday_move_pct': 0,
            'gap_pct': 0
        }

        # Calculate metrics
        price_range = high - low
        price_range_pct = (price_range / open_price * 100) if open_price > 0 else 0
        details['price_range_pct'] = price_range_pct

        intraday_move = abs(current_price - open_price)
        intraday_move_pct = (intraday_move / open_price * 100) if open_price > 0 else 0
        details['intraday_move_pct'] = intraday_move_pct

        gap = open_price - prev_close
        gap_pct = (gap / prev_close * 100) if prev_close > 0 else 0
        details['gap_pct'] = gap_pct

        # Trend detection
        if current_price > open_price and open_price >= prev_close:
            if intraday_move_pct > 1.0:
                return MarketRegime.TRENDING_UP, details

        if current_price < open_price and open_price <= prev_close:
            if intraday_move_pct > 1.0:
                return MarketRegime.TRENDING_DOWN, details

        # Volatility detection
        if price_range_pct > 3.0 or abs(gap_pct) > 2.0:
            return MarketRegime.HIGH_VOLATILITY, details

        if price_range_pct < 0.5:
            return MarketRegime.LOW_VOLATILITY, details

        # Range-bound
        if 0.5 <= price_range_pct <= 2.0 and abs(gap_pct) < 1.0:
            return MarketRegime.RANGING, details

        return MarketRegime.UNKNOWN, details


class StrategyManager:
    """Manages multiple trading strategies with regime awareness"""

    def __init__(self, settings: Dict, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.regime_detector = RegimeDetector(logger)

        # Strategy toggles
        self.strategies_enabled = settings.get('strategies', {})

        # Base strategy weights (DEPRECATED - kept for backward compatibility)
        self.base_weights = {
            'momentum': 0.22,
            'mean_reversion': 0.18,
            'news': 0.10,
            'volume': 0.08,
            'earnings': 0.05,
            'longterm_trend': 0.13,
            'longterm_momentum': 0.10,
            'crypto': 0.05,
            'forex': 0.05,
            'etf': 0.04
        }

        # Fixed strategy confidence values (intrinsic reliability)
        # These represent how much we trust each strategy based on historical performance
        self.strategy_confidence = {
            'momentum': 0.75,        # High reliability in trending markets
            'mean_reversion': 0.65,  # Good in ranging markets
            'news': 0.70,            # High impact when present
            'volume': 0.70,          # Strong indicator of interest
            'earnings': 0.75,        # High reliability near earnings
            'longterm_trend': 0.65,  # Good for sustained moves
            'longterm_momentum': 0.65,  # Good for sustained moves
            'crypto': 0.60,          # Specialized, volatile
            'forex': 0.70,           # Technical analysis driven
            'etf': 0.68              # Diversified, less volatile
        }

        # Regime-specific strategy preferences
        self.regime_weights = {
            MarketRegime.TRENDING_UP: {
                'momentum': 0.30,
                'mean_reversion': 0.04,
                'volume': 0.13,
                'news': 0.07,
                'earnings': 0.02,
                'longterm_trend': 0.18,
                'longterm_momentum': 0.09,
                'crypto': 0.05,
                'forex': 0.08,
                'etf': 0.04
            },
            MarketRegime.TRENDING_DOWN: {
                'momentum': 0.08,
                'mean_reversion': 0.32,
                'volume': 0.13,
                'news': 0.10,
                'earnings': 0.04,
                'longterm_trend': 0.07,
                'longterm_momentum': 0.09,
                'crypto': 0.05,
                'forex': 0.08,
                'etf': 0.04
            },
            MarketRegime.RANGING: {
                'momentum': 0.04,
                'mean_reversion': 0.35,
                'volume': 0.09,
                'news': 0.13,
                'earnings': 0.04,
                'longterm_trend': 0.13,
                'longterm_momentum': 0.04,
                'crypto': 0.04,
                'forex': 0.10,
                'etf': 0.04
            },
            MarketRegime.HIGH_VOLATILITY: {
                'momentum': 0.18,
                'mean_reversion': 0.13,
                'volume': 0.22,
                'news': 0.13,
                'earnings': 0.04,
                'longterm_trend': 0.07,
                'longterm_momentum': 0.06,
                'crypto': 0.05,
                'forex': 0.08,
                'etf': 0.04
            }
        }

        # Asset-specific universes
        self.crypto_universe = settings.get('crypto', {}).get('universe', ['BTC/USD', 'ETH/USD'])
        self.forex_universe = settings.get('forex', {}).get('universe', ['EUR/USD', 'GBP/USD'])
        self.etf_universe = settings.get('etf', {}).get('universe', ['SPY', 'QQQ', 'IWM'])
        self.logger.info(f"Crypto universe: {self.crypto_universe}")
        self.logger.info(f"Forex universe: {self.forex_universe}")
        self.logger.info(f"ETF universe: {self.etf_universe}")

    def score_momentum(self, current_price: float, open_price: float, prev_close: float, high: float, low: float) -> Tuple[float, Dict]:
        """Score momentum strategy"""
        details = {}

        intraday_change = (current_price - open_price) / open_price if open_price > 0 else 0
        details['intraday_change_pct'] = intraday_change * 100

        gap = (open_price - prev_close) / prev_close if prev_close > 0 else 0
        details['gap_pct'] = gap * 100

        price_range = high - low
        if price_range > 0:
            position_in_range = (current_price - low) / price_range
        else:
            position_in_range = 0.5
        details['position_in_range'] = position_in_range

        score = (intraday_change * 5.0 + gap * 2.0 + (position_in_range - 0.5) * 0.3)
        score = max(0, min(1, (score + 0.5)))
        details['raw_score'] = score

        return score, details

    def score_mean_reversion(self, current_price: float, open_price: float, prev_close: float, high: float, low: float) -> Tuple[float, Dict]:
        """Score mean reversion strategy"""
        details = {}

        deviation = (prev_close - current_price) / prev_close if prev_close > 0 else 0
        details['deviation_pct'] = deviation * 100

        price_range = high - low
        if price_range > 0:
            position_in_range = (current_price - low) / price_range
        else:
            position_in_range = 0.5
        details['position_in_range'] = position_in_range

        if deviation > 0:
            score = deviation * 5.0 + (1 - position_in_range) * 0.3
        else:
            score = 0

        score = max(0, min(1, score))
        details['raw_score'] = score

        return score, details

    def score_news(self, symbol: str, news_data, window_hours: int = 6) -> Tuple[float, Dict]:
        """
        Score based on news activity and sentiment.
        Delegates to strategies.score_news for actual scoring logic.
        """
        return strat.score_news(symbol, news_data, window_hours)

    def score_volume(self, current_volume: Optional[float], avg_volume: Optional[float]) -> Tuple[float, Dict]:
        """Score based on volume"""
        details = {}

        if not current_volume or not avg_volume or avg_volume == 0:
            return 0.5, {'note': 'no_volume_data'}

        volume_ratio = current_volume / avg_volume
        details['volume_ratio'] = volume_ratio

        if volume_ratio > 1.0:
            score = min(1.0, 0.5 + (volume_ratio - 1.0) * 0.2)
        else:
            score = 0.5 * volume_ratio

        details['score'] = score
        return score, details

    def score_earnings(self, symbol: str, earnings_calendar: Dict[str, Dict], days_until_limit: int = 7) -> Tuple[float, Dict]:
        """Score based on upcoming earnings"""
        details = {}

        if symbol not in earnings_calendar:
            return 0, {'note': 'no_earnings_scheduled'}

        earnings_info = earnings_calendar[symbol]
        days_until = earnings_info.get('days_until', 999)
        details['days_until'] = days_until

        if days_until <= days_until_limit:
            score = 1.0 - (days_until / days_until_limit)
        else:
            score = 0

        details['score'] = score
        return score, details

    def score_longterm_trend(self, current_price: float, prev_close: float, snapshot: Dict) -> Tuple[float, Dict]:
        """
        Score long-term trend strength
        Uses longer timeframe price action
        """
        details = {}
        
        # Try to get multi-day price history if available
        prev_bars = snapshot.get('prevDailyBar', {})
        
        # Calculate trend over available period
        if prev_close > 0:
            # Simple trend: current vs previous close
            trend_change = (current_price - prev_close) / prev_close
            details['trend_change_pct'] = trend_change * 100
            
            # Score: positive for uptrends, scaled to 0-1
            if trend_change > 0:
                score = min(1.0, trend_change * 10)  # Scale up small moves
            else:
                score = max(0.0, 0.5 + trend_change * 10)  # Downtrends score below 0.5
        else:
            score = 0.5
            details['note'] = 'insufficient_data'
        
        details['score'] = score
        return score, details

    def score_longterm_momentum(self, current_price: float, open_price: float, prev_close: float, snapshot: Dict) -> Tuple[float, Dict]:
        """
        Score long-term momentum
        Looks at sustained directional movement
        """
        details = {}
        
        # Calculate momentum indicators
        if prev_close > 0 and open_price > 0:
            # Price momentum
            price_momentum = (current_price - prev_close) / prev_close
            details['price_momentum_pct'] = price_momentum * 100
            
            # Gap momentum
            gap_momentum = (open_price - prev_close) / prev_close
            details['gap_momentum_pct'] = gap_momentum * 100
            
            # Combined momentum score
            if price_momentum > 0 and gap_momentum > 0:
                # Both positive - strong momentum
                score = min(1.0, (price_momentum + gap_momentum) * 5)
            elif price_momentum > 0:
                # Only price positive - moderate momentum
                score = min(0.7, price_momentum * 5)
            else:
                # Negative momentum
                score = max(0.0, 0.5 + price_momentum * 5)
        else:
            score = 0.5
            details['note'] = 'insufficient_data'
        
        details['score'] = score
        return score, details

    def score_crypto(self, symbol: str, current_price: float, open_price: float, prev_close: float, high: float, low: float) -> Tuple[float, Dict]:
        """
        Score cryptocurrency-specific factors
        Crypto markets are 24/7 and more volatile
        """
        details = {}
        
        # Check if this is a crypto symbol
        is_crypto = '/' in symbol or symbol in ['BTC', 'ETH', 'BTCUSD', 'ETHUSD']
        details['is_crypto'] = is_crypto
        
        if not is_crypto:
            return 0, {'note': 'not_crypto'}
        
        # Crypto-specific scoring
        # 1. Volatility is expected and not necessarily bad
        price_range = high - low
        price_range_pct = (price_range / open_price * 100) if open_price > 0 else 0
        details['price_range_pct'] = price_range_pct
        
        # 2. 24/7 movement
        intraday_move = abs(current_price - open_price)
        intraday_move_pct = (intraday_move / open_price * 100) if open_price > 0 else 0
        details['intraday_move_pct'] = intraday_move_pct
        
        # 3. Trend direction
        trend = (current_price - prev_close) / prev_close if prev_close > 0 else 0
        details['trend_pct'] = trend * 100
        
        # Score: favor strong trends and volatility in crypto
        if trend > 0:
            # Uptrend: higher volatility = higher score
            score = min(1.0, (trend * 5) + (price_range_pct / 10))
        else:
            # Downtrend: lower score but not zero
            score = max(0.2, 0.5 + (trend * 3))
        
        details['score'] = score
        return score, details

    def is_crypto_symbol(self, symbol: str) -> bool:
        """Check if symbol is a cryptocurrency"""
        # Check if in crypto universe
        if symbol in self.crypto_universe:
            return True

        # Check common crypto patterns
        crypto_patterns = ['/', 'USD', 'BTC', 'ETH', 'LTC', 'DOGE', 'ADA', 'SOL']
        return any(pattern in symbol for pattern in crypto_patterns)

    def is_forex_symbol(self, symbol: str) -> bool:
        """Check if symbol is a forex pair"""
        # Check if in forex universe
        if symbol in self.forex_universe:
            return True

        # Check if symbol contains forex currency codes
        forex_currencies = ['EUR', 'GBP', 'USD', 'JPY', 'CHF', 'AUD', 'NZD', 'CAD']
        if '/' in symbol:
            parts = symbol.split('/')
            if len(parts) == 2 and all(part in forex_currencies for part in parts):
                return True

        return False

    def is_etf_symbol(self, symbol: str) -> bool:
        """Check if symbol is an ETF"""
        # Check if in ETF universe
        if symbol in self.etf_universe:
            return True

        # Common ETF patterns
        # Most sector ETFs start with 'XL', many end with 'ETF'
        if symbol.startswith('XL') or symbol.endswith('ETF'):
            return True

        return False

    def rank_candidates(self, snapshots: Dict, news_data, earnings_calendar: Dict, min_score: float = 0.5) -> List[CombinedSignal]:
        """
        Rank all candidates using combined signals.

        Args:
            snapshots: Market data snapshots
            news_data: Either List[NewsArticle] or Dict[str, int] for backward compatibility
            earnings_calendar: Earnings data
            min_score: Minimum score threshold

        Returns:
            List of ranked CombinedSignal objects
        """
        candidates = []

        for symbol, snapshot in snapshots.items():
            try:
                current_price = snapshot.get('latestTrade', {}).get('p', 0)
                if current_price <= 0:
                    continue

                open_price = snapshot.get('dailyBar', {}).get('o', current_price)
                prev_close = snapshot.get('prevDailyBar', {}).get('c', open_price)
                high = snapshot.get('dailyBar', {}).get('h', current_price)
                low = snapshot.get('dailyBar', {}).get('l', current_price)
                volume = snapshot.get('dailyBar', {}).get('v')
                avg_volume = snapshot.get('prevDailyBar', {}).get('v')

                # Check asset types
                is_crypto = self.is_crypto_symbol(symbol)
                is_forex = self.is_forex_symbol(symbol)
                is_etf = self.is_etf_symbol(symbol)

                # Detect regime
                regime, regime_details = self.regime_detector.detect_regime(
                    current_price, open_price, prev_close, high, low, volume
                )

                # Get regime-specific weights
                weights = self.regime_weights.get(regime, self.base_weights)

                signals = []

                # Momentum signal
                if self.strategies_enabled.get('momentum', True):
                    score, details = self.score_momentum(current_price, open_price, prev_close, high, low)
                    regime_match = regime in [MarketRegime.TRENDING_UP, MarketRegime.HIGH_VOLATILITY]
                    signals.append(SignalResult(
                        strategy_name='momentum',
                        score=score,
                        confidence=self.strategy_confidence.get('momentum', 0.75),
                        regime_match=regime_match,
                        details=details
                    ))

                # Mean reversion signal
                if self.strategies_enabled.get('mean_reversion', True):
                    score, details = self.score_mean_reversion(current_price, open_price, prev_close, high, low)
                    regime_match = regime in [MarketRegime.RANGING, MarketRegime.TRENDING_DOWN]
                    signals.append(SignalResult(
                        strategy_name='mean_reversion',
                        score=score,
                        confidence=self.strategy_confidence.get('mean_reversion', 0.65),
                        regime_match=regime_match,
                        details=details
                    ))

                # News signal
                if self.strategies_enabled.get('news', True):
                    score, details = self.score_news(symbol, news_data)
                    # News only matches regime when it has meaningful signal
                    regime_match = score > 0.3
                    signals.append(SignalResult(
                        strategy_name='news',
                        score=score,
                        confidence=self.strategy_confidence.get('news', 0.70),
                        regime_match=regime_match,
                        details=details
                    ))

                # Volume signal
                if self.strategies_enabled.get('volume', True):
                    score, details = self.score_volume(volume, avg_volume)
                    # Volume only matches regime when it shows significant activity
                    regime_match = score > 0.6
                    signals.append(SignalResult(
                        strategy_name='volume',
                        score=score,
                        confidence=self.strategy_confidence.get('volume', 0.70),
                        regime_match=regime_match,
                        details=details
                    ))

                # Earnings signal
                if self.strategies_enabled.get('earnings', False):
                    score, details = self.score_earnings(symbol, earnings_calendar)
                    # Earnings only matches regime when event is imminent
                    regime_match = score > 0.5
                    signals.append(SignalResult(
                        strategy_name='earnings',
                        score=score,
                        confidence=self.strategy_confidence.get('earnings', 0.75),
                        regime_match=regime_match,
                        details=details
                    ))

                # Long-term trend signal
                if self.strategies_enabled.get('longterm_trend', False):
                    score, details = self.score_longterm_trend(current_price, prev_close, snapshot)
                    regime_match = regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
                    signals.append(SignalResult(
                        strategy_name='longterm_trend',
                        score=score,
                        confidence=self.strategy_confidence.get('longterm_trend', 0.65),
                        regime_match=regime_match,
                        details=details
                    ))

                # Long-term momentum signal
                if self.strategies_enabled.get('longterm_momentum', False):
                    score, details = self.score_longterm_momentum(current_price, open_price, prev_close, snapshot)
                    regime_match = regime in [MarketRegime.TRENDING_UP]
                    signals.append(SignalResult(
                        strategy_name='longterm_momentum',
                        score=score,
                        confidence=self.strategy_confidence.get('longterm_momentum', 0.65),
                        regime_match=regime_match,
                        details=details
                    ))

                # Crypto signal
                if self.strategies_enabled.get('crypto', False) and is_crypto:
                    score, details = self.score_crypto(symbol, current_price, open_price, prev_close, high, low)
                    # Crypto only matches regime when showing strong signal
                    regime_match = score > 0.5
                    signals.append(SignalResult(
                        strategy_name='crypto',
                        score=score,
                        confidence=self.strategy_confidence.get('crypto', 0.60),
                        regime_match=regime_match,
                        details=details
                    ))

                # Forex signal
                if self.strategies_enabled.get('forex', False) and is_forex:
                    score, details = strat.score_forex(symbol, current_price, open_price, prev_close, high, low)
                    # Forex works well in all regimes but especially ranging
                    regime_match = regime in [MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
                    signals.append(SignalResult(
                        strategy_name='forex',
                        score=score,
                        confidence=self.strategy_confidence.get('forex', 0.70),
                        regime_match=regime_match,
                        details=details
                    ))

                # ETF signal
                if self.strategies_enabled.get('etf', False) and is_etf:
                    score, details = strat.score_etf(symbol, current_price, open_price, prev_close, high, low, volume, avg_volume)
                    # ETF works best in trending markets
                    regime_match = regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
                    signals.append(SignalResult(
                        strategy_name='etf',
                        score=score,
                        confidence=self.strategy_confidence.get('etf', 0.68),
                        regime_match=regime_match,
                        details=details
                    ))

                # STAGE 1: Filter to regime-matching strategies only
                matching_signals = [s for s in signals if s.regime_match]

                # Track all signals for metadata
                all_active_strategies = [s.strategy_name for s in signals if s.score > 0.3]

                # Need at least some matching strategies to proceed
                if not matching_signals:
                    continue

                # STAGE 2: Weight by fixed intrinsic confidence
                total_weight = 0
                weighted_sum = 0
                matching_active_strategies = []

                for signal in matching_signals:
                    # Use fixed confidence based on strategy reliability
                    weight = self.strategy_confidence.get(signal.strategy_name, 0.5)

                    weighted_sum += signal.score * weight
                    total_weight += weight

                    if signal.score > 0.3:
                        matching_active_strategies.append(signal.strategy_name)

                final_score = weighted_sum / total_weight if total_weight > 0 else 0

                # Confidence is mean of fixed confidence values for matching strategies
                confidence = statistics.mean([
                    self.strategy_confidence.get(s.strategy_name, 0.5)
                    for s in matching_signals
                ]) if matching_signals else 0

                if final_score >= min_score:
                    candidates.append(CombinedSignal(
                        symbol=symbol,
                        final_score=final_score,
                        confidence=confidence,
                        active_strategies=matching_active_strategies,
                        regime=regime,
                        signals=signals,
                        metadata={
                            'regime_details': regime_details,
                            'current_price': current_price,
                            'matching_strategies_count': len(matching_signals),
                            'total_strategies_count': len(signals),
                            'non_matching_strategies': [s.strategy_name for s in signals if not s.regime_match]
                        },
                        is_crypto=is_crypto
                    ))

            except Exception as e:
                self.logger.error(f"Error scoring {symbol}: {e}")

        candidates.sort(key=lambda x: x.final_score, reverse=True)
        self.logger.info(f"Ranked {len(candidates)} candidates (filtered {len(snapshots) - len(candidates)} below {min_score})")

        return candidates

    def get_entry_signal(
        self,
        signal: CombinedSignal,
        entry_threshold: float = 0.62,
        strategy_config: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Determine if signal is strong enough for entry.
        Uses strategy-specific configuration if provided.
        Only considers regime-matching strategies in evaluation.
        """
        # Use strategy-specific threshold if available
        if strategy_config:
            entry_threshold = strategy_config.get('entry_threshold', entry_threshold)

        if signal.final_score < entry_threshold:
            return False, f"Score {signal.final_score:.2f} < threshold {entry_threshold}"

        # active_strategies now only includes regime-matching strategies
        if len(signal.active_strategies) < 2:
            return False, f"Only {len(signal.active_strategies)} matching active strategies"

        # Confidence is now calculated from matching strategies only
        if signal.confidence < 0.3:
            return False, f"Low confidence {signal.confidence:.2f} (from matching strategies)"

        if signal.regime == MarketRegime.HIGH_VOLATILITY:
            # Crypto can handle high volatility better
            if not signal.is_crypto and signal.final_score < entry_threshold + 0.1:
                return False, "High volatility requires higher score (non-crypto)"

        return True, f"Strong signal: {signal.final_score:.2f}"

    def calculate_stop_loss(
        self,
        entry_price: float,
        regime: MarketRegime,
        base_stop_bps: float = 50.0,
        is_crypto: bool = False,
        strategy_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate stop loss price based on regime and asset type.
        Uses strategy-specific stop loss if provided in config.
        """
        # If strategy config provided, use its stop_loss_pct as base
        if strategy_config and 'stop_loss_pct' in strategy_config:
            # strategy_config has stop_loss_pct (e.g., 0.8 = 0.8%)
            # Convert to basis points: 0.8% = 80 bps
            base_stop_bps = strategy_config['stop_loss_pct'] * 100

        regime_multipliers = {
            MarketRegime.TRENDING_UP: 0.8,
            MarketRegime.TRENDING_DOWN: 1.2,
            MarketRegime.RANGING: 1.0,
            MarketRegime.HIGH_VOLATILITY: 1.5,
            MarketRegime.LOW_VOLATILITY: 0.7,
            MarketRegime.UNKNOWN: 1.0
        }

        multiplier = regime_multipliers.get(regime, 1.0)

        # Crypto needs wider stops due to higher volatility
        if is_crypto:
            multiplier *= 2.0

        adjusted_stop_bps = base_stop_bps * multiplier
        stop_loss = entry_price * (1 - adjusted_stop_bps / 10000.0)

        return stop_loss
