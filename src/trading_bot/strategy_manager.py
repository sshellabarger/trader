"""
Enhanced Strategy Manager with Regime Detection
Includes: momentum, mean_reversion, news, volume, earnings, longterm_trend, longterm_momentum, crypto
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

        # Base strategy weights
        self.base_weights = {
            'momentum': 0.25,
            'mean_reversion': 0.20,
            'news': 0.10,
            'volume': 0.08,
            'earnings': 0.05,
            'longterm_trend': 0.15,
            'longterm_momentum': 0.12,
            'crypto': 0.05
        }

        # Regime-specific strategy preferences
        self.regime_weights = {
            MarketRegime.TRENDING_UP: {
                'momentum': 0.35,
                'mean_reversion': 0.05,
                'volume': 0.15,
                'news': 0.08,
                'earnings': 0.02,
                'longterm_trend': 0.20,
                'longterm_momentum': 0.10,
                'crypto': 0.05
            },
            MarketRegime.TRENDING_DOWN: {
                'momentum': 0.10,
                'mean_reversion': 0.35,
                'volume': 0.15,
                'news': 0.12,
                'earnings': 0.05,
                'longterm_trend': 0.08,
                'longterm_momentum': 0.10,
                'crypto': 0.05
            },
            MarketRegime.RANGING: {
                'momentum': 0.05,
                'mean_reversion': 0.40,
                'volume': 0.10,
                'news': 0.15,
                'earnings': 0.05,
                'longterm_trend': 0.15,
                'longterm_momentum': 0.05,
                'crypto': 0.05
            },
            MarketRegime.HIGH_VOLATILITY: {
                'momentum': 0.20,
                'mean_reversion': 0.15,
                'volume': 0.25,
                'news': 0.15,
                'earnings': 0.05,
                'longterm_trend': 0.08,
                'longterm_momentum': 0.07,
                'crypto': 0.05
            }
        }

        # Crypto-specific universe
        self.crypto_universe = settings.get('crypto', {}).get('universe', ['BTC/USD', 'ETH/USD'])
        self.logger.info(f"Crypto universe: {self.crypto_universe}")

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

                # Check if crypto
                is_crypto = self.is_crypto_symbol(symbol)

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
                        confidence=weights.get('momentum', 0.25),
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
                        confidence=weights.get('mean_reversion', 0.20),
                        regime_match=regime_match,
                        details=details
                    ))

                # News signal
                if self.strategies_enabled.get('news', True):
                    score, details = self.score_news(symbol, news_data)
                    signals.append(SignalResult(
                        strategy_name='news',
                        score=score,
                        confidence=weights.get('news', 0.10),
                        regime_match=True,
                        details=details
                    ))

                # Volume signal
                if self.strategies_enabled.get('volume', True):
                    score, details = self.score_volume(volume, avg_volume)
                    signals.append(SignalResult(
                        strategy_name='volume',
                        score=score,
                        confidence=weights.get('volume', 0.08),
                        regime_match=True,
                        details=details
                    ))

                # Earnings signal
                if self.strategies_enabled.get('earnings', False):
                    score, details = self.score_earnings(symbol, earnings_calendar)
                    signals.append(SignalResult(
                        strategy_name='earnings',
                        score=score,
                        confidence=weights.get('earnings', 0.05),
                        regime_match=True,
                        details=details
                    ))

                # Long-term trend signal
                if self.strategies_enabled.get('longterm_trend', False):
                    score, details = self.score_longterm_trend(current_price, prev_close, snapshot)
                    regime_match = regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
                    signals.append(SignalResult(
                        strategy_name='longterm_trend',
                        score=score,
                        confidence=weights.get('longterm_trend', 0.15),
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
                        confidence=weights.get('longterm_momentum', 0.12),
                        regime_match=regime_match,
                        details=details
                    ))

                # Crypto signal
                if self.strategies_enabled.get('crypto', False) and is_crypto:
                    score, details = self.score_crypto(symbol, current_price, open_price, prev_close, high, low)
                    signals.append(SignalResult(
                        strategy_name='crypto',
                        score=score,
                        confidence=weights.get('crypto', 0.05),
                        regime_match=True,
                        details=details
                    ))

                # Calculate weighted final score
                total_weight = 0
                weighted_sum = 0
                active_strategies = []

                for signal in signals:
                    weight = signal.confidence
                    if not signal.regime_match:
                        weight *= 0.5

                    weighted_sum += signal.score * weight
                    total_weight += weight

                    if signal.score > 0.3:
                        active_strategies.append(signal.strategy_name)

                final_score = weighted_sum / total_weight if total_weight > 0 else 0
                confidence = statistics.mean([s.confidence for s in signals]) if signals else 0

                if final_score >= min_score:
                    candidates.append(CombinedSignal(
                        symbol=symbol,
                        final_score=final_score,
                        confidence=confidence,
                        active_strategies=active_strategies,
                        regime=regime,
                        signals=signals,
                        metadata={
                            'regime_details': regime_details,
                            'current_price': current_price,
                            'weights_used': weights
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
        """
        # Use strategy-specific threshold if available
        if strategy_config:
            entry_threshold = strategy_config.get('entry_threshold', entry_threshold)

        if signal.final_score < entry_threshold:
            return False, f"Score {signal.final_score:.2f} < threshold {entry_threshold}"

        if len(signal.active_strategies) < 2:
            return False, f"Only {len(signal.active_strategies)} active strategies"

        if signal.confidence < 0.3:
            return False, f"Low confidence {signal.confidence:.2f}"

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
