#!/usr/bin/env python3
"""
Analyze Batch Test Results and Optimize Strategy Settings

This script analyzes the results from batch_test_strategies.py and generates
optimized parameter recommendations for each strategy. It can also automatically
update the strategy_configs.py file with the improved settings.

Usage:
    # Analyze results and show recommendations
    python analyze_and_optimize_settings.py test_results/

    # Analyze and automatically update strategy_configs.py
    python analyze_and_optimize_settings.py test_results/ --apply

    # Show detailed analysis for specific strategy
    python analyze_and_optimize_settings.py test_results/ --strategy momentum --detailed
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_bot.strategy_testing import StrategyType
from trading_bot.strategy_configs import get_strategy_config, STRATEGY_CONFIGS

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StrategyAnalysis:
    """Analysis results for a single strategy"""
    def __init__(self, strategy_name: str, metrics: Dict[str, Any]):
        self.strategy_name = strategy_name
        self.metrics = metrics
        self.recommendations = []
        self.new_settings = {}
        self.performance_score = 0.0

    def calculate_performance_score(self) -> float:
        """
        Calculate composite performance score
        Higher is better
        """
        # Weights for different metrics
        weights = {
            'win_rate': 0.25,
            'profit_factor': 0.20,
            'sharpe_ratio': 0.20,
            'total_pnl': 0.20,
            'max_drawdown_pct': -0.15,  # Negative because lower is better
        }

        score = 0.0

        # Win rate (0-1 scale)
        if self.metrics.get('win_rate'):
            score += weights['win_rate'] * self.metrics['win_rate']

        # Profit factor (normalize to 0-1, assuming 3.0 is excellent)
        if self.metrics.get('profit_factor'):
            pf = min(self.metrics['profit_factor'] / 3.0, 1.0)
            score += weights['profit_factor'] * pf

        # Sharpe ratio (normalize to 0-1, assuming 2.0 is excellent)
        if self.metrics.get('sharpe_ratio'):
            sharpe = min(max(self.metrics['sharpe_ratio'] / 2.0, 0.0), 1.0)
            score += weights['sharpe_ratio'] * sharpe

        # Total P&L (normalize based on starting capital)
        if self.metrics.get('total_pnl') and self.metrics.get('starting_capital'):
            pnl_ratio = self.metrics['total_pnl'] / self.metrics['starting_capital']
            pnl_score = min(max(pnl_ratio * 10, 0.0), 1.0)  # 10% return = 1.0
            score += weights['total_pnl'] * pnl_score

        # Max drawdown (lower is better)
        if self.metrics.get('max_drawdown_pct'):
            dd_penalty = abs(self.metrics['max_drawdown_pct']) / 0.10  # 10% DD = -1.0
            score += weights['max_drawdown_pct'] * min(dd_penalty, 1.0)

        self.performance_score = max(score, 0.0)
        return self.performance_score

    def analyze_and_recommend(self):
        """Analyze metrics and generate recommendations"""
        m = self.metrics

        # Get current config for comparison
        try:
            strategy_type = StrategyType[self.strategy_name.upper()]
            current_config = get_strategy_config(strategy_type)
        except (KeyError, ValueError):
            current_config = {}

        # Analyze win rate
        if m.get('total_trades', 0) < 5:
            self.recommendations.append(
                "⚠️  Insufficient trades for reliable analysis (need at least 5 trades)"
            )
            return

        win_rate = m.get('win_rate', 0.0)

        # Win rate analysis
        if win_rate < 0.40:
            self.recommendations.append(
                f"❌ Low win rate ({win_rate:.1%}) - Consider raising entry_threshold to be more selective"
            )
            # Suggest higher entry threshold
            current_entry = current_config.get('entry_threshold', 0.5)
            self.new_settings['entry_threshold'] = min(current_entry + 0.05, 0.80)

        elif win_rate > 0.65:
            self.recommendations.append(
                f"✅ High win rate ({win_rate:.1%}) - Consider lowering entry_threshold to capture more opportunities"
            )
            # Suggest lower entry threshold
            current_entry = current_config.get('entry_threshold', 0.5)
            self.new_settings['entry_threshold'] = max(current_entry - 0.05, 0.40)

        # Profit factor analysis
        profit_factor = m.get('profit_factor', 0.0)
        if profit_factor < 1.0:
            self.recommendations.append(
                f"❌ Losing strategy (profit factor: {profit_factor:.2f}) - Strategy needs major adjustment"
            )
        elif profit_factor < 1.5:
            self.recommendations.append(
                f"⚠️  Marginal profit factor ({profit_factor:.2f}) - Consider tightening stop loss or widening take profit"
            )
        elif profit_factor > 2.5:
            self.recommendations.append(
                f"✅ Excellent profit factor ({profit_factor:.2f}) - Strategy is working well"
            )

        # Avg win vs avg loss analysis
        avg_win = abs(m.get('avg_win', 0.0))
        avg_loss = abs(m.get('avg_loss', 0.0))

        if avg_loss > 0:
            win_loss_ratio = avg_win / avg_loss

            if win_loss_ratio < 1.0:
                self.recommendations.append(
                    f"⚠️  Average losses (${avg_loss:.2f}) exceed average wins (${avg_win:.2f})"
                )
                # Suggest tighter stop loss
                current_sl = current_config.get('stop_loss_pct', 1.0)
                self.new_settings['stop_loss_pct'] = max(current_sl * 0.9, 0.3)

            elif win_loss_ratio > 2.0:
                self.recommendations.append(
                    f"✅ Average wins (${avg_win:.2f}) are much larger than losses (${avg_loss:.2f})"
                )
                # Can potentially widen stop loss for fewer false stops
                current_sl = current_config.get('stop_loss_pct', 1.0)
                self.new_settings['stop_loss_pct'] = min(current_sl * 1.1, 3.0)

        # Max drawdown analysis
        max_dd = abs(m.get('max_drawdown_pct', 0.0))
        if max_dd > 0.10:  # 10%
            self.recommendations.append(
                f"⚠️  High max drawdown ({max_dd:.2%}) - Consider reducing position_size_pct"
            )
            current_pos_size = current_config.get('position_size_pct', 2.0)
            self.new_settings['position_size_pct'] = max(current_pos_size * 0.9, 1.0)

        elif max_dd < 0.02:  # 2%
            self.recommendations.append(
                f"✅ Low max drawdown ({max_dd:.2%}) - Could potentially increase position size"
            )
            current_pos_size = current_config.get('position_size_pct', 2.0)
            self.new_settings['position_size_pct'] = min(current_pos_size * 1.1, 4.0)

        # Sharpe ratio analysis
        sharpe = m.get('sharpe_ratio', 0.0)
        if sharpe < 0.5:
            self.recommendations.append(
                f"⚠️  Low Sharpe ratio ({sharpe:.2f}) - Returns not compensating for risk"
            )
        elif sharpe > 1.5:
            self.recommendations.append(
                f"✅ Excellent Sharpe ratio ({sharpe:.2f}) - Strong risk-adjusted returns"
            )

        # Exit reason analysis
        exit_reasons = m.get('exit_reasons', {})
        if exit_reasons:
            stop_loss_pct = exit_reasons.get('stop_loss', 0.0)
            take_profit_pct = exit_reasons.get('take_profit', 0.0)
            time_exit_pct = exit_reasons.get('time', 0.0)

            if stop_loss_pct > 0.60:  # 60%+ stop losses
                self.recommendations.append(
                    f"⚠️  High stop loss rate ({stop_loss_pct:.1%}) - Consider widening stop loss or raising entry threshold"
                )
                current_sl = current_config.get('stop_loss_pct', 1.0)
                self.new_settings['stop_loss_pct'] = min(current_sl * 1.15, 3.0)

            if take_profit_pct < 0.15 and stop_loss_pct < 0.50:
                # Few take profits, but not too many stop losses
                self.recommendations.append(
                    f"⚠️  Low take profit rate ({take_profit_pct:.1%}) - Consider lowering take_profit_pct target"
                )
                current_tp = current_config.get('take_profit_pct', 2.0)
                self.new_settings['take_profit_pct'] = max(current_tp * 0.9, 1.0)

            if time_exit_pct > 0.50:
                self.recommendations.append(
                    f"⚠️  Many time-based exits ({time_exit_pct:.1%}) - Consider shortening max_hold_minutes"
                )
                current_hold = current_config.get('max_hold_minutes', 240)
                self.new_settings['max_hold_minutes'] = max(int(current_hold * 0.85), 60)

        # Fill in any missing settings with current values
        for key in ['entry_threshold', 'exit_threshold', 'stop_loss_pct', 'take_profit_pct',
                    'max_hold_minutes', 'position_size_pct']:
            if key not in self.new_settings and key in current_config:
                self.new_settings[key] = current_config[key]


def load_latest_results(results_dir: str) -> Dict[str, Dict]:
    """Load the most recent test results for each strategy"""
    results_path = Path(results_dir)

    if not results_path.exists():
        logger.error(f"Results directory not found: {results_dir}")
        sys.exit(1)

    strategy_results = {}

    # Find all *_test_*.json files (not trades or signals)
    test_files = list(results_path.glob("*_test_*.json"))

    if not test_files:
        logger.error(f"No test result files found in {results_dir}")
        sys.exit(1)

    # Group by strategy and take the most recent
    strategy_files = {}
    for filepath in test_files:
        # Extract strategy name from filename (e.g., "momentum_test_20240101_120000.json")
        filename = filepath.name
        if '_test_' not in filename:
            continue

        strategy_name = filename.split('_test_')[0]

        # Use file modification time to get most recent
        if strategy_name not in strategy_files or filepath.stat().st_mtime > strategy_files[strategy_name].stat().st_mtime:
            strategy_files[strategy_name] = filepath

    # Load the results
    for strategy_name, filepath in strategy_files.items():
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                strategy_results[strategy_name] = data
                logger.info(f"Loaded results for {strategy_name} from {filepath.name}")
        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")

    return strategy_results


def generate_updated_config_file(analyses: List[StrategyAnalysis], output_path: str):
    """Generate updated strategy_configs.py file with new settings"""

    header = '''"""
Strategy-Specific Configuration Parameters

Each strategy has its own optimal parameters for:
- Entry threshold
- Exit threshold
- Stop loss percentage
- Take profit percentage
- Maximum hold time
- Test duration

These settings have been optimized based on live testing results.
Last updated: {timestamp}
"""

from typing import Dict, Any


# Strategy-specific parameter configurations
# Using string keys to avoid circular import with StrategyType enum
STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {{
'''.format(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    config_entries = []

    for analysis in analyses:
        strategy_name = analysis.strategy_name
        settings = analysis.new_settings

        if not settings:
            # Use existing settings if no recommendations
            try:
                strategy_type = StrategyType[strategy_name.upper()]
                settings = get_strategy_config(strategy_type)
            except (KeyError, ValueError):
                continue

        # Build config entry
        entry = f'''    "{strategy_name}": {{
        'entry_threshold': {settings.get('entry_threshold', 0.5):.2f},
        'exit_threshold': {settings.get('exit_threshold', 0.3):.2f},
        'stop_loss_pct': {settings.get('stop_loss_pct', 1.0):.2f},
        'take_profit_pct': {settings.get('take_profit_pct', 2.0):.2f},
        'max_hold_minutes': {int(settings.get('max_hold_minutes', 240))},
        'default_duration': {int(settings.get('default_duration', 60))},
        'position_size_pct': {settings.get('position_size_pct', 2.0):.2f},
    }},
'''
        config_entries.append(entry)

    # Add the rest of the file
    footer = '''
}


def get_strategy_config(strategy) -> Dict[str, Any]:
    """
    Get configuration parameters for a specific strategy

    Args:
        strategy: The strategy type (StrategyType enum or string)

    Returns:
        Dictionary of configuration parameters
    """
    # Handle both StrategyType enum and string inputs
    if hasattr(strategy, 'value'):
        strategy_key = strategy.value
    else:
        strategy_key = str(strategy)

    return STRATEGY_CONFIGS.get(strategy_key, {
        # Default fallback configuration
        'entry_threshold': 0.5,
        'exit_threshold': 0.3,
        'stop_loss_pct': 0.5,
        'take_profit_pct': 2.0,
        'max_hold_minutes': 240,
        'default_duration': 60,
        'position_size_pct': 2.0,
    })


def get_all_strategy_configs() -> Dict[str, Dict[str, Any]]:
    """Get all strategy configurations"""
    return STRATEGY_CONFIGS.copy()


def print_strategy_config(strategy):
    """Print configuration for a strategy"""
    config = get_strategy_config(strategy)
    strategy_name = strategy.value if hasattr(strategy, 'value') else str(strategy)
    print(f"\\n{'='*60}")
    print(f"Configuration for {strategy_name.upper()}")
    print(f"{'='*60}")
    for key, value in config.items():
        print(f"  {key:20s}: {value}")
    print(f"{'='*60}\\n")


def print_all_configs():
    """Print all strategy configurations"""
    print("\\n" + "="*80)
    print("STRATEGY-SPECIFIC CONFIGURATIONS")
    print("="*80)

    for strategy_key in STRATEGY_CONFIGS.keys():
        config = STRATEGY_CONFIGS[strategy_key]
        print(f"\\n{strategy_key.upper()}:")
        for key, value in config.items():
            print(f"  {key:25s}: {value}")

    print("\\n" + "="*80)
'''

    # Combine all parts
    full_content = header + ''.join(config_entries) + footer

    # Write to file
    with open(output_path, 'w') as f:
        f.write(full_content)

    logger.info(f"Generated updated config file: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze batch test results and optimize strategy settings'
    )

    parser.add_argument(
        'results_dir',
        type=str,
        help='Directory containing test results'
    )

    parser.add_argument(
        '--apply',
        action='store_true',
        help='Apply recommended changes to strategy_configs.py'
    )

    parser.add_argument(
        '--strategy',
        type=str,
        help='Analyze specific strategy only'
    )

    parser.add_argument(
        '--detailed',
        action='store_true',
        help='Show detailed metrics'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='src/trading_bot/strategy_configs_optimized.py',
        help='Output file for optimized configs (default: strategy_configs_optimized.py)'
    )

    args = parser.parse_args()

    # Load results
    logger.info(f"Loading results from {args.results_dir}...")
    strategy_results = load_latest_results(args.results_dir)

    if not strategy_results:
        logger.error("No valid results found")
        sys.exit(1)

    logger.info(f"Found results for {len(strategy_results)} strategies\n")

    # Analyze each strategy
    analyses = []

    for strategy_name, metrics in strategy_results.items():
        # Filter by specific strategy if requested
        if args.strategy and strategy_name.lower() != args.strategy.lower():
            continue

        analysis = StrategyAnalysis(strategy_name, metrics)
        analysis.calculate_performance_score()
        analysis.analyze_and_recommend()
        analyses.append(analysis)

    # Sort by performance score
    analyses.sort(key=lambda a: a.performance_score, reverse=True)

    # Print analysis
    logger.info(f"{'='*80}")
    logger.info("STRATEGY ANALYSIS AND RECOMMENDATIONS")
    logger.info(f"{'='*80}\n")

    for analysis in analyses:
        logger.info(f"\n{'='*80}")
        logger.info(f"{analysis.strategy_name.upper()}")
        logger.info(f"{'='*80}")
        logger.info(f"Performance Score: {analysis.performance_score:.2f} / 1.0")

        m = analysis.metrics

        logger.info(f"\nCurrent Performance:")
        logger.info(f"  Total Trades: {m.get('total_trades', 0)}")
        logger.info(f"  Win Rate: {m.get('win_rate', 0.0):.1%}")
        logger.info(f"  Total P&L: ${m.get('total_pnl', 0.0):.2f}")
        logger.info(f"  Profit Factor: {m.get('profit_factor', 0.0):.2f}")
        logger.info(f"  Sharpe Ratio: {m.get('sharpe_ratio', 0.0):.2f}")
        logger.info(f"  Max Drawdown: {abs(m.get('max_drawdown_pct', 0.0)):.2%}")

        if args.detailed:
            logger.info(f"\nDetailed Metrics:")
            logger.info(f"  Avg Win: ${abs(m.get('avg_win', 0.0)):.2f}")
            logger.info(f"  Avg Loss: ${abs(m.get('avg_loss', 0.0)):.2f}")
            logger.info(f"  Largest Win: ${abs(m.get('largest_win', 0.0)):.2f}")
            logger.info(f"  Largest Loss: ${abs(m.get('largest_loss', 0.0)):.2f}")

            exit_reasons = m.get('exit_reasons', {})
            if exit_reasons:
                logger.info(f"\nExit Reasons:")
                for reason, pct in exit_reasons.items():
                    logger.info(f"  {reason}: {pct:.1%}")

        logger.info(f"\nRecommendations:")
        if analysis.recommendations:
            for rec in analysis.recommendations:
                logger.info(f"  {rec}")
        else:
            logger.info("  ✅ No major issues found")

        if analysis.new_settings:
            logger.info(f"\nSuggested Settings Changes:")
            try:
                strategy_type = StrategyType[analysis.strategy_name.upper()]
                current_config = get_strategy_config(strategy_type)

                for key, new_value in analysis.new_settings.items():
                    old_value = current_config.get(key)
                    if old_value != new_value:
                        logger.info(f"  {key}: {old_value} → {new_value}")
            except (KeyError, ValueError):
                for key, value in analysis.new_settings.items():
                    logger.info(f"  {key}: {value}")

    # Generate updated config file
    if args.apply:
        logger.info(f"\n{'='*80}")
        logger.info("APPLYING OPTIMIZED SETTINGS")
        logger.info(f"{'='*80}\n")

        # Backup original file
        original_config = 'src/trading_bot/strategy_configs.py'
        backup_config = f'src/trading_bot/strategy_configs_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.py'

        if os.path.exists(original_config):
            import shutil
            shutil.copy(original_config, backup_config)
            logger.info(f"Backed up original config to: {backup_config}")

        # Generate new config
        generate_updated_config_file(analyses, args.output)

        if args.output != original_config:
            logger.info(f"\nGenerated optimized config: {args.output}")
            logger.info(f"Review the changes and copy to {original_config} when ready")
        else:
            logger.info(f"\nUpdated {original_config} with optimized settings")

    else:
        logger.info(f"\n{'='*80}")
        logger.info("DRY RUN - No changes applied")
        logger.info(f"{'='*80}")
        logger.info("Run with --apply to update strategy_configs.py")

    logger.info("")


if __name__ == '__main__':
    main()
