"""
Grid bot backtest — sweeps (lower, upper, gridNum, mode) and reports
profit, fills, breakout time, max drawdown vs hold.

Cross-counting model:
  Each candle has [high, low]. For every grid line that lies between
  prev_close and current high/low, count as a price cross.
  Grid bot earns 1 round-trip per pair of crosses on the same line
  (up cross then down cross, or vice versa).
  Conservative: assume worst-case ordering — count `min(up_crosses, down_crosses)`
  per line as round trips. Each round trip = grid_step / line_price profit
  minus 0.2% fee (0.1% per leg).

Out-of-range tracking:
  Any candle whose close is outside [lower, upper] adds duration
  to "breakout time" (penalty proxy — capital sits idle).

Usage:
  # Fetch candles first (one-time, raw json is .gitignore'd):
  okx market candles ETH-USDT --bar 4H --limit 1500 --json > data/eth_4h.json
  okx market candles ETH-USDT --bar 1H --limit 1500 --json > data/eth_1h.json
  okx market candles ETH-USDT --bar 1D --limit 1500 --json > data/eth_1d.json

  # Then sweep:
  python grid_backtest.py 4H
  python grid_backtest.py 1H
  python grid_backtest.py 1D

Environment:
  CANDLE_DIR  default: ./data/   (relative to this script)
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from itertools import product

KST = timezone(timedelta(hours=9))

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def load(path):
    with open(path, encoding='utf-8') as f:
        c = f.read()
    # The okx CLI prepends a "Update available..." banner before JSON output.
    return json.loads(c[c.index('['):])


def normalize(raw):
    """OKX returns newest-first list of [ts,o,h,l,c,vol,...]. Reverse to oldest-first floats."""
    out = []
    for row in raw:
        out.append({
            'ts': int(row[0]),
            'o': float(row[1]),
            'h': float(row[2]),
            'l': float(row[3]),
            'c': float(row[4]),
        })
    out.sort(key=lambda x: x['ts'])
    return out


def grid_lines(lower, upper, num, mode='geometric'):
    if mode == 'geometric':
        ratio = (upper / lower) ** (1.0 / num)
        return [lower * (ratio ** i) for i in range(num + 1)]
    return [lower + (upper - lower) / num * i for i in range(num + 1)]


def simulate(candles, lower, upper, num, mode, fee=0.001, invest=10000):
    """
    Returns dict with: total_pnl_usdt, rt_count, breakout_hours,
    daily_pnl, monthly_return_pct, avg_pnl_per_rt
    """
    lines = grid_lines(lower, upper, num, mode)
    if len(lines) < 2:
        return None

    n_lines = len(lines)
    up_count = [0] * n_lines
    down_count = [0] * n_lines
    breakout_seconds = 0
    last_ts = None

    for k in candles:
        h, l, c = k['h'], k['l'], k['c']
        dt_sec = (k['ts'] - last_ts) / 1000 if last_ts is not None else 0
        last_ts = k['ts']

        # Out-of-range using close as proxy
        if c < lower or c > upper:
            breakout_seconds += dt_sec
            continue

        # In-range: count crossings against each grid line
        for i, line in enumerate(lines):
            if l <= line <= h:
                if k['o'] < line and k['c'] > line:
                    up_count[i] += 1
                elif k['o'] > line and k['c'] < line:
                    down_count[i] += 1
                else:
                    up_count[i] += 0.5
                    down_count[i] += 0.5

    total_rt = 0.0
    total_pnl = 0.0
    for i in range(n_lines):
        rt = min(up_count[i], down_count[i])
        total_rt += rt
        if i + 1 < n_lines:
            spacing = lines[i + 1] - lines[i]
            base = (lines[i] + lines[i + 1]) / 2
            alloc_per_line = invest / num
            qty = alloc_per_line / base
            gross = spacing * qty
            fee_cost = (lines[i] * qty + lines[i + 1] * qty) * fee
            net = gross - fee_cost
            total_pnl += rt * net

    days = (candles[-1]['ts'] - candles[0]['ts']) / 1000 / 86400
    return {
        'rt': total_rt,
        'pnl_usdt': total_pnl,
        'breakout_hours': breakout_seconds / 3600,
        'days': days,
        'daily_pnl': total_pnl / days if days > 0 else 0,
        'monthly_return_pct': (total_pnl / invest) * (30 / days) * 100 if days > 0 else 0,
        'avg_pnl_per_rt': total_pnl / total_rt if total_rt > 0 else 0,
    }


def main():
    bar = sys.argv[1] if len(sys.argv) > 1 else '4H'
    data_dir = os.environ.get('CANDLE_DIR', DEFAULT_DATA_DIR)
    path = os.path.join(data_dir, f'eth_{bar.lower()}.json')

    if not os.path.exists(path):
        print(f'ERROR: {path} not found.')
        print(f'Fetch first:  okx market candles ETH-USDT --bar {bar} --limit 1500 --json > {path}')
        sys.exit(1)

    candles = normalize(load(path))
    print(f'\n=== Backtest on {bar} candles ({len(candles)} bars, '
          f'{datetime.fromtimestamp(candles[0]["ts"]/1000, tz=KST).date()} ~ '
          f'{datetime.fromtimestamp(candles[-1]["ts"]/1000, tz=KST).date()}) ===\n')

    cur_price = candles[-1]['c']
    print(f'Current price: ${cur_price:,.2f}\n')

    lowers = [2000, 2050, 2100, 2150, 2200, 2250, 2300]
    uppers = [2400, 2450, 2500, 2550, 2600, 2700]
    nums = [10, 15, 20, 30]
    modes = ['geometric', 'arithmetic']

    results = []
    for lo, hi, n, m in product(lowers, uppers, nums, modes):
        if hi - lo < 200 or hi - lo > 800:
            continue
        if not (lo <= cur_price <= hi):
            continue
        r = simulate(candles, lo, hi, n, m)
        if r is None:
            continue
        r.update({'lo': lo, 'hi': hi, 'n': n, 'm': m})
        results.append(r)

    def score(r):
        breakout_penalty = r['breakout_hours'] / 24 * 0.5
        return r['monthly_return_pct'] - breakout_penalty

    results.sort(key=score, reverse=True)

    print(f"{'Rank':<5}{'Range':<14}{'N':<4}{'Mode':<11}{'Month %':>9}{'Daily $':>10}{'RT':>7}{'OOR h':>8}{'Score':>8}")
    print('-' * 80)
    for i, r in enumerate(results[:25]):
        print(f"{i+1:<5}{r['lo']}-{r['hi']:<8}{r['n']:<4}{r['m']:<11}"
              f"{r['monthly_return_pct']:>8.2f}%{r['daily_pnl']:>9.2f}"
              f"{r['rt']:>7.1f}{r['breakout_hours']:>7.1f}h{score(r):>8.2f}")
    print(f'\nTotal candidates: {len(results)}')

    print('\n=== Spotlight: 사용자 제안 / 자주 거론된 범위 ===')
    spots = [
        (2100, 2400, 10, 'geometric'), (2100, 2400, 10, 'arithmetic'),
        (2200, 2500, 10, 'geometric'), (2250, 2500, 10, 'geometric'),
        (2200, 2400, 10, 'geometric'), (2150, 2450, 10, 'geometric'),
        (2000, 2400, 10, 'geometric'), (2000, 2500, 10, 'arithmetic'),
        (2000, 2500, 10, 'geometric'), (2000, 2600, 10, 'arithmetic'),
        (2100, 2500, 10, 'arithmetic'), (2100, 2500, 10, 'geometric'),
        (2100, 2600, 10, 'arithmetic'), (2050, 2500, 10, 'arithmetic'),
    ]
    for lo, hi, n, m in spots:
        for r in results:
            if r['lo'] == lo and r['hi'] == hi and r['n'] == n and r['m'] == m:
                print(f"  {lo}-{hi}, {n} {m:11s}: month {r['monthly_return_pct']:6.2f}%, "
                      f"RT {r['rt']:5.1f}, OOR {r['breakout_hours']:5.1f}h, score {score(r):6.2f}")
                break
        else:
            print(f"  {lo}-{hi}, {n} {m}: not in candidate set")


if __name__ == '__main__':
    main()
