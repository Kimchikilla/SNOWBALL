"""
grid_controller.py
OKX REST API를 이용해 그리드봇을 제어합니다.
"""

import hmac, hashlib, base64, time, json
from datetime import datetime, timezone
from typing import Optional
import httpx

_MAX_RETRIES = 3
_RETRY_DELAY = 2

# config 핫리로드 호환을 위해 from-import 대신 config.X로 참조한다.
import config


class GridController:
    """
    OKX Spot Grid Bot 제어 클래스.

    이벤트 기반 액션:
      MAINTAIN  → ensure_grid_running()
      WIDEN     → widen_grid()
      SHIFT     → shift_grid_center()
      STOP      → emergency_stop()
    """

    def __init__(self):
        self.bot_id: Optional[str] = None      # 실행 중인 봇 ID
        self.current_lower: Optional[float] = None   # 현재 그리드 하한
        self.current_upper: Optional[float] = None   # 현재 그리드 상한
        self.current_grid_num: Optional[int] = None  # 현재 그리드 개수
        self.current_mode: Optional[str] = None      # arithmetic / geometric
        self.client = httpx.Client(
            base_url=config.OKX_BASE_URL,
            timeout=httpx.Timeout(config.OKX_TIMEOUT_SEC, connect=10.0),
        )

    # ─── 유틸리티 ─────────────────────────────────────────────

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        """Safely parse a float value from an API response."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    # ─── 기존 봇 동기화 ────────────────────────────────────────

    def sync_existing_bot(self) -> dict:
        """
        OKX에서 현재 심볼에 대해 실행 중인 그리드봇을 조회하고,
        있으면 에이전트 상태를 동기화합니다.
        Returns: 동기화 결과 dict (status, bot_id 등)
        """
        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/orders-algo-pending",
                params={
                    "algoOrdType": "grid",
                    "instType": "SPOT",
                    "instId": config.SYMBOL,
                }
            )

            if resp.get("code") != "0":
                self._log(f"기존 봇 조회 실패: code={resp.get('code')} msg={resp.get('msg', '')}", level="ERROR")
                return {"status": "query_failed", "resp": resp}

            bots = resp.get("data", [])
            if not isinstance(bots, list):
                return {"status": "no_bots"}

            # 현재 심볼과 일치하는 봇 찾기
            for bot in bots:
                if not isinstance(bot, dict):
                    continue
                if bot.get("instId") != config.SYMBOL:
                    continue

                # 동기화
                self.bot_id = bot.get("algoId")
                self.current_lower = self._safe_float(bot.get("minPx"))
                self.current_upper = self._safe_float(bot.get("maxPx"))
                grid_num = bot.get("gridNum", "?")
                run_type = bot.get("runType", "1")
                mode = "arithmetic" if run_type == "1" else "geometric"
                try:
                    self.current_grid_num = int(grid_num)
                except (ValueError, TypeError):
                    self.current_grid_num = None
                self.current_mode = mode
                state = bot.get("state", "unknown")
                investment = self._safe_float(bot.get("investment"))
                total_pnl = self._safe_float(bot.get("totalPnl"))
                grid_profit = self._safe_float(bot.get("gridProfit"))
                float_pnl = self._safe_float(bot.get("floatProfit"))

                self._log(
                    f"✅ 기존 그리드봇 감지 | bot_id={self.bot_id}\n"
                    f"     심볼: {config.SYMBOL} | 상태: {state}\n"
                    f"     범위: {self.current_lower:,.2f} ~ {self.current_upper:,.2f}\n"
                    f"     그리드: {grid_num}개 ({mode})\n"
                    f"     투자금: {investment:,.2f} USDT\n"
                    f"     손익: 그리드={grid_profit:+,.2f} 평가={float_pnl:+,.2f} 합계={total_pnl:+,.2f}"
                )

                return {
                    "code": "0",
                    "status": "synced",
                    "bot_id": self.bot_id,
                    "lower": self.current_lower,
                    "upper": self.current_upper,
                    "grid_num": grid_num,
                    "mode": mode,
                    "state": state,
                    "investment": investment,
                    "total_pnl": total_pnl,
                }

            return {"status": "no_bots"}

        except Exception as e:
            self._log(f"기존 봇 동기화 실패: {e}", level="ERROR")
            return {"status": "error", "msg": str(e)}

    def list_active_bots(self) -> list[dict]:
        """OKX의 모든 활성 그리드봇 리스트 반환 (심볼 무관, 멀티봇 표시용).

        텔레그램 알림 footer에 어떤 봇들이 돌고 있는지 보여주는 용도.
        실패 시 빈 리스트 반환 (호출자가 footer 생략 처리).
        """
        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/orders-algo-pending",
                params={"algoOrdType": "grid", "instType": "SPOT"}
            )
            if resp.get("code") != "0":
                return []
            bots = resp.get("data", [])
            if not isinstance(bots, list):
                return []
            return [b for b in bots if isinstance(b, dict)]
        except Exception:
            return []

    # ─── 공개 액션 메서드 ────────────────────────────────────

    def ensure_grid_running(self, lower=None, upper=None, count=None) -> dict:
        """기존 봇 동기화 시도 → 없으면 새로 시작."""
        if self.bot_id:
            return {"code": "0", "status": "already_running", "bot_id": self.bot_id}

        # 먼저 OKX에서 기존 봇 확인
        sync = self.sync_existing_bot()
        if sync.get("status") == "synced":
            return sync
        if sync.get("status") not in ("no_bots",):
            self._log(
                f"기존 봇 조회가 실패하여 새 그리드 생성을 중단합니다 "
                f"(status={sync.get('status')}, msg={sync.get('msg', sync.get('resp', ''))})",
                level="ERROR",
            )
            return {
                "code": "-1",
                "status": "sync_failed",
                "msg": "existing grid lookup failed; refusing to start a duplicate bot",
                "sync": sync,
            }

        # 없으면 새로 시작
        return self.start_grid(lower, upper, count)

    def start_grid(self, lower=None, upper=None, count=None, mode=None,
                   budget=None) -> dict:
        """새 그리드봇을 시작합니다."""
        lower = lower or config.GRID_LOWER
        upper = upper or config.GRID_UPPER
        count = count or config.GRID_COUNT
        mode = mode or self.current_mode or config.GRID_MODE
        budget = float(budget or config.GRID_BUDGET)

        # 잔고 프리체크: OKX 에러로 죽기 전에 명확한 사유를 돌려준다.
        # 봇 정지 직후엔 자금 해제가 늦을 수 있어 짧게 재시도한다.
        avail = None
        for attempt in range(3):
            avail = self.get_quote_balance()
            if avail is None or avail >= budget:
                break
            time.sleep(3)
        if avail is not None and avail < budget:
            self._log(
                f"그리드 시작 차단: {self._quote_ccy()} 가용 잔고 {avail:,.2f} < "
                f"필요 예산 {budget:,.2f}",
                level="ERROR",
            )
            return {
                "code": "-1",
                "status": "insufficient_balance",
                "available": avail,
                "required": budget,
            }

        body = {
            "instId":       config.SYMBOL,
            "algoOrdType":  "grid",
            "maxPx":        str(upper),
            "minPx":        str(lower),
            "gridNum":      str(count),
            "runType":      "1" if mode == "arithmetic" else "2",
            "quoteSz":      str(budget),
        }
        resp = self._post("/api/v5/tradingBot/grid/order-algo", body)

        if resp.get("code") == "0":
            try:
                data = resp.get("data")
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                    self.bot_id = data[0].get("algoId")
                else:
                    self._log(f"그리드봇 시작 응답 구조 이상: {resp}", level="ERROR")
                self.current_lower = float(lower)
                self.current_upper = float(upper)
                self.current_grid_num = int(count)
                self.current_mode = mode
                self._log(f"그리드봇 시작 | bot_id={self.bot_id} | 범위={lower}~{upper} | {count}개 그리드")
            except Exception as e:
                self._log(f"그리드봇 시작 응답 파싱 실패: {e}", level="ERROR")
        else:
            sMsg = ""
            if isinstance(resp.get("data"), list) and resp["data"]:
                sMsg = resp["data"][0].get("sMsg", "")
            self._log(f"그리드봇 시작 실패: code={resp.get('code')} sMsg={sMsg}", level="ERROR")

        return resp

    def widen_grid(self, atr_value: float, current_price: float) -> dict:
        """
        CAUTION 상태: 그리드 간격을 ATR x 2배 기준으로 넓힙니다.
        기존 봇을 중지하고 더 넓은 범위로 재시작합니다.
        """
        if not self.bot_id:
            return {"status": "no_bot"}

        current_range = (
            self.current_upper - self.current_lower
            if self.current_lower is not None and self.current_upper is not None
            else config.GRID_UPPER - config.GRID_LOWER
        )
        # WIDEN must never shrink the active grid. ATR from 1m candles can be tiny,
        # so use it only as a floor alongside the current range.
        new_range = max(current_range * 1.25, atr_value * 8, current_price * 0.08)
        half_range = new_range / 2
        new_lower  = max(current_price - half_range, current_price * 0.2, 0.0001)
        new_upper  = current_price + half_range
        if new_upper <= new_lower:
            new_lower = current_price - current_range / 2
            new_upper = current_price + current_range / 2

        self._log(f"그리드 간격 확대 | 새 범위={new_lower:.0f}~{new_upper:.0f} (ATR={atr_value:.1f})")

        self.stop_grid(sell_remaining=False)
        return self.start_grid(
            lower=new_lower,
            upper=new_upper,
            count=self.current_grid_num or config.GRID_COUNT,
            mode=self.current_mode or config.GRID_MODE,
        )

    def emergency_stop(self, verify: bool = True) -> dict:
        """
        EMERGENCY 상태: 모든 포지션을 시장가로 즉시 청산합니다.

        2026-06-04 사고 교훈: stopType 매핑 버그로 "청산" 알림 후에도
        ETH 37.5개가 남아 있었다. 이제 정지 후 잔고를 실제로 검증하고,
        남은 물량이 있으면 시장가로 직접 매도한다.
        """
        self._log("긴급 청산 실행 (EMERGENCY)", level="CRITICAL")
        result = self.stop_grid(sell_remaining=True)
        self.bot_id = None

        if not verify:
            return result

        verification = self.verify_liquidated()
        result["verification"] = verification
        if not verification.get("flat", False):
            self._log(
                f"⚠️ 청산 검증 실패: {verification.get('remaining_qty', 0):.6f} "
                f"{self._base_ccy()} 잔존 → 시장가 직접 매도 시도",
                level="CRITICAL",
            )
            result["flatten"] = self.flatten_spot()
            result["verification"] = self.verify_liquidated()
        return result

    # ─── 청산 검증 / 현물 직접 매도 ──────────────────────────

    @staticmethod
    def _base_ccy() -> str:
        return config.SYMBOL.split("-")[0]

    @staticmethod
    def _quote_ccy() -> str:
        parts = config.SYMBOL.split("-")
        return parts[1] if len(parts) > 1 else "USDT"

    def get_base_balance(self) -> float:
        """기초자산(예: ETH) 현물 가용 잔고."""
        balances = self.get_account_balance()
        info = balances.get(self._base_ccy(), {})
        return float(info.get("available", 0.0) or 0.0)

    def get_quote_balance(self) -> Optional[float]:
        """호가자산(예: USDT) 가용 잔고. 조회 실패 시 None (차단하지 않음)."""
        balances = self.get_account_balance()
        if not balances:
            return None
        info = balances.get(self._quote_ccy(), {})
        return float(info.get("available", 0.0) or 0.0)

    def verify_liquidated(self, dust_usd: float = 10.0,
                          max_attempts: int = 6, wait_sec: float = 5.0) -> dict:
        """봇 정지 후 기초자산이 실제로 청산됐는지 검증.

        봇 정지 직후엔 자산 해제/매도 체결에 시간이 걸릴 수 있어
        wait_sec 간격으로 max_attempts회까지 재확인한다.
        """
        price = self.get_last_price() or 0.0
        remaining = 0.0
        for attempt in range(1, max_attempts + 1):
            remaining = self.get_base_balance()
            value = remaining * price
            if value <= dust_usd:
                return {"flat": True, "remaining_qty": remaining,
                        "remaining_usd": value, "attempts": attempt}
            if attempt < max_attempts:
                time.sleep(wait_sec)
        return {"flat": False, "remaining_qty": remaining,
                "remaining_usd": remaining * price, "attempts": max_attempts}

    def get_last_price(self) -> Optional[float]:
        """현재가 조회 (public ticker)."""
        try:
            resp = self._get("/api/v5/market/ticker", params={"instId": config.SYMBOL})
            data = resp.get("data", [])
            if isinstance(data, list) and data:
                return self._safe_float(data[0].get("last")) or None
        except Exception as e:
            self._log(f"현재가 조회 실패: {e}", level="ERROR")
        return None

    def spot_market_sell(self, qty: float) -> dict:
        """현물 시장가 매도 (sz = 기초자산 수량)."""
        if qty <= 0:
            return {"code": "-1", "msg": "qty must be positive"}
        body = {
            "instId": config.SYMBOL,
            "tdMode": "cash",
            "side": "sell",
            "ordType": "market",
            "sz": f"{qty:.6f}",
            "tgtCcy": "base_ccy",
        }
        resp = self._post("/api/v5/trade/order", body)
        if resp.get("code") == "0":
            self._log(f"현물 시장가 매도 | {qty:.6f} {self._base_ccy()}")
        else:
            self._log(f"현물 시장가 매도 실패: {resp.get('msg', resp)}", level="ERROR")
        return resp

    def flatten_spot(self) -> dict:
        """가용 기초자산 전량 시장가 매도."""
        qty = self.get_base_balance()
        if qty <= 0:
            return {"code": "0", "msg": "no base balance"}
        return self.spot_market_sell(qty)

    def stop_grid(self, sell_remaining: bool = False) -> dict:
        """그리드봇을 중지합니다."""
        if not self.bot_id:
            return {"status": "no_bot"}

        # OKX spot grid stopType:
        # "1" = stop and sell all holdings, "2" = stop and keep holdings.
        body = [{
            "algoId":      self.bot_id,
            "instId":      config.SYMBOL,
            "algoOrdType": "grid",
            "stopType":    "1" if sell_remaining else "2",
        }]

        resp = self._post("/api/v5/tradingBot/grid/stop-order-algo", body)

        if resp.get("code") == "0":
            self._log(f"그리드봇 중지 | sell_remaining={sell_remaining}")
            self.bot_id = None
        else:
            self._log(f"그리드봇 중지 실패: code={resp.get('code')} msg={resp.get('msg', '')}", level="ERROR")

        return resp

    def get_bot_status(self) -> dict:
        """현재 봇 상태와 PnL 조회."""
        if not self.bot_id:
            return {"status": "no_bot"}

        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/orders-algo-details",
                params={"algoId": self.bot_id, "algoOrdType": "grid"}
            )
            if not isinstance(resp, dict):
                return {"code": "-1", "msg": "unexpected response type"}
            return resp
        except Exception as e:
            self._log(f"get_bot_status 실패: {e}", level="ERROR")
            return {"code": "-1", "msg": str(e)}

    def get_recent_fills(self, limit: int = 20) -> list[dict]:
        """최근 체결 내역 조회."""
        try:
            resp = self._get(
                "/api/v5/trade/fills-history",
                params={"instId": config.SYMBOL, "limit": str(limit)}
            )
            data = resp.get("data", [])
            if not isinstance(data, list):
                self._log(f"get_recent_fills 응답 'data' 타입 이상: {type(data)}", level="ERROR")
                return []
            return data
        except Exception as e:
            self._log(f"get_recent_fills 실패: {e}", level="ERROR")
            return []

    def get_grid_pnl(self) -> dict:
        """그리드봇 수익 정보 조회."""
        if not self.bot_id:
            return {}
        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/orders-algo-details",
                params={"algoId": self.bot_id, "algoOrdType": "grid"}
            )
            if resp.get("code") == "0" and resp.get("data"):
                data_list = resp.get("data")
                if not isinstance(data_list, list) or len(data_list) == 0:
                    return {}
                data = data_list[0]
                if not isinstance(data, dict):
                    return {}
                return {
                    "grid_profit": self._safe_float(data.get("gridProfit")),
                    "float_profit": self._safe_float(data.get("floatProfit")),
                    "total_pnl": self._safe_float(data.get("totalPnl")),
                    "annualized_rate": self._safe_float(data.get("annualizedRate")),
                    "investment": self._safe_float(data.get("investment")),
                }
        except Exception as e:
            self._log(f"get_grid_pnl 실패: {e}", level="ERROR")
        return {}

    def get_account_balance(self) -> dict:
        """OKX 계좌 잔고 조회 (현물). 코인별 보유량 + USDT 잔고."""
        try:
            resp = self._get("/api/v5/account/balance")
            if resp.get("code") != "0" or not resp.get("data"):
                return {}

            data = resp["data"][0] if isinstance(resp["data"], list) and resp["data"] else {}
            details = data.get("details", [])
            if not isinstance(details, list):
                return {}

            balances = {}
            for d in details:
                if not isinstance(d, dict):
                    continue
                ccy = d.get("ccy", "")
                avail = self._safe_float(d.get("availBal"))
                frozen = self._safe_float(d.get("frozenBal"))
                total = self._safe_float(d.get("cashBal"))
                eq_usd = self._safe_float(d.get("eqUsd"))
                if total > 0 or avail > 0 or frozen > 0:
                    balances[ccy] = {
                        "available": avail,
                        "frozen": frozen,
                        "total": total,
                        "eq_usd": eq_usd,
                    }
            return balances
        except Exception as e:
            self._log(f"계좌 잔고 조회 실패: {e}", level="ERROR")
            return {}

    def get_grid_positions(self) -> dict:
        """그리드봇의 상세 포지션 정보 조회."""
        if not self.bot_id:
            return {}
        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/orders-algo-details",
                params={"algoId": self.bot_id, "algoOrdType": "grid"}
            )
            if resp.get("code") != "0" or not resp.get("data"):
                return {}
            data = resp["data"][0] if isinstance(resp["data"], list) else {}
            if not isinstance(data, dict):
                return {}

            return {
                "state": data.get("state", "unknown"),
                "investment": self._safe_float(data.get("investment")),
                "grid_profit": self._safe_float(data.get("gridProfit")),
                "float_profit": self._safe_float(data.get("floatProfit")),
                "total_pnl": self._safe_float(data.get("totalPnl")),
                "filled_count": data.get("filledCount", "0"),
                "total_count": data.get("totalCount", "0"),
                "annualized_rate": self._safe_float(data.get("annualizedRate")),
                "base_sz": self._safe_float(data.get("baseSz")),  # 보유 코인 수량
                "quote_sz": self._safe_float(data.get("quoteSz")),  # 투입 USDT
                "cur_base_sz": self._safe_float(data.get("curBaseSz")),  # 현재 코인 보유
                "cur_quote_sz": self._safe_float(data.get("curQuoteSz")),  # 현재 USDT 잔여
            }
        except Exception as e:
            self._log(f"그리드 포지션 조회 실패: {e}", level="ERROR")
            return {}

    def get_pending_orders(self) -> dict:
        """그리드봇 서브 주문을 매수/매도로 분류해서 반환."""
        if not self.bot_id:
            return {"buy": [], "sell": []}
        try:
            resp = self._get(
                "/api/v5/tradingBot/grid/sub-orders",
                params={
                    "algoId": self.bot_id,
                    "algoOrdType": "grid",
                    "type": "live",
                }
            )
            orders = resp.get("data", [])
            if not isinstance(orders, list):
                return {"buy": [], "sell": []}

            buys = []
            sells = []
            for o in orders:
                if not isinstance(o, dict):
                    continue
                side = o.get("side", "")
                px = self._safe_float(o.get("px"))
                sz = self._safe_float(o.get("sz"))
                if px <= 0:
                    continue
                entry = {"price": px, "size": sz, "amount": px * sz}
                if side == "buy":
                    buys.append(entry)
                elif side == "sell":
                    sells.append(entry)

            buys.sort(key=lambda x: x["price"], reverse=True)
            sells.sort(key=lambda x: x["price"], reverse=True)
            return {"buy": buys, "sell": sells}
        except Exception as e:
            self._log(f"그리드봇 서브 주문 조회 실패: {e}", level="ERROR")
            return {"buy": [], "sell": []}

    def get_today_fills(self) -> dict:
        """당일 그리드봇 체결 내역 집계.
        왕복(round trip) = min(매수, 매도), 순수익 = 왕복 × (간격 × 수량) - 수수료.
        """
        empty = {"buy_count": 0, "sell_count": 0, "total_count": 0,
                 "round_trips": 0, "gross_per_trip": 0, "fee_per_trip": 0,
                 "net_per_trip": 0, "net_profit": 0, "total_fees": 0}
        if not self.bot_id:
            return empty
        try:
            # 당일 00:00 기준 밀리초
            now = datetime.now()
            today_start = datetime(now.year, now.month, now.day)
            today_ms = int(today_start.timestamp() * 1000)

            # 페이지네이션으로 당일 체결 전부 수집 (최대 5페이지 = 500건)
            orders = []
            after = ""
            for _ in range(5):
                params = {
                    "algoId": self.bot_id,
                    "algoOrdType": "grid",
                    "type": "filled",
                }
                if after:
                    params["after"] = after
                resp = self._get("/api/v5/tradingBot/grid/sub-orders", params)
                page = resp.get("data", [])
                if not isinstance(page, list) or not page:
                    break
                # 당일 이전 데이터 나오면 중단
                oldest_time = int(page[-1].get("fillTime", page[-1].get("uTime", 0)))
                orders.extend(page)
                if oldest_time < today_ms:
                    break
                # 다음 페이지 커서
                after = page[-1].get("ordId", "")
                if not after:
                    break

            buy_count = 0
            sell_count = 0
            buy_fees_coin = 0.0   # 매수 수수료 (코인 단위)
            sell_fees_usdt = 0.0  # 매도 수수료 (USDT 단위)
            sizes = []
            last_price = 0.0

            for o in orders:
                if not isinstance(o, dict):
                    continue
                fill_time = int(o.get("fillTime", o.get("uTime", 0)))
                if fill_time < today_ms:
                    continue

                side = o.get("side", "")
                px = self._safe_float(o.get("px"))
                sz = self._safe_float(o.get("sz"))
                fee = abs(self._safe_float(o.get("fee")))
                sizes.append(sz)
                if px > 0:
                    last_price = px

                if side == "buy":
                    buy_count += 1
                    buy_fees_coin += fee  # ETH로 차감
                elif side == "sell":
                    sell_count += 1
                    sell_fees_usdt += fee  # USDT로 차감

            # 매수 수수료를 현재가로 USDT 환산
            buy_fees_usdt = buy_fees_coin * last_price if last_price > 0 else 0
            total_fees = buy_fees_usdt + sell_fees_usdt

            # 왕복 = 완성된 매수→매도 사이클
            round_trips = min(buy_count, sell_count)

            # 그리드 간격
            spacing = 0.0
            if (self.current_lower is not None and self.current_upper is not None
                    and self.current_grid_num and self.current_grid_num > 0):
                spacing = (self.current_upper - self.current_lower) / self.current_grid_num

            # 평균 체결 수량
            avg_sz = sum(sizes) / len(sizes) if sizes else 0

            # 1회 왕복 수익
            gross_per_trip = spacing * avg_sz
            fee_per_trip = total_fees / round_trips if round_trips > 0 else 0
            net_per_trip = gross_per_trip - fee_per_trip

            # 총 순수익
            net_profit = round_trips * net_per_trip

            return {
                "buy_count": buy_count,
                "sell_count": sell_count,
                "total_count": buy_count + sell_count,
                "round_trips": round_trips,
                "avg_size": avg_sz,
                "spacing": spacing,
                "gross_per_trip": gross_per_trip,
                "fee_per_trip": fee_per_trip,
                "net_per_trip": net_per_trip,
                "net_profit": net_profit,
                "total_fees": total_fees,
            }
        except Exception as e:
            self._log(f"당일 체결 집계 실패: {e}", level="ERROR")
            return empty

    # ─── 그리드 중심 이동 & 노출 축소 ──────────────────────────

    def shift_grid_center(self, new_center: float, current_price: float,
                          grid_range: float = None) -> dict:
        """
        그리드 중심을 new_center로 이동합니다 (trailing grid).
        grid_range가 None이면 현재 config.GRID_UPPER - config.GRID_LOWER 폭을 그대로 사용합니다.
        """
        if not self.bot_id:
            return {"status": "no_bot"}

        if grid_range is None:
            if self.current_lower is not None and self.current_upper is not None:
                grid_range = self.current_upper - self.current_lower
            else:
                grid_range = config.GRID_UPPER - config.GRID_LOWER

        new_lower = new_center - grid_range / 2
        new_upper = new_center + grid_range / 2

        self._log(
            f"그리드 중심 이동 | new_center={new_center:.2f} "
            f"| 새 범위={new_lower:.2f}~{new_upper:.2f} "
            f"| current_price={current_price:.2f}"
        )

        self.stop_grid(sell_remaining=False)
        resp = self.start_grid(
            lower=new_lower,
            upper=new_upper,
            count=self.current_grid_num or config.GRID_COUNT,
            mode=self.current_mode or config.GRID_MODE,
        )
        return resp

    # ─── 주문 관리 ───────────────────────────────────────────

    def _cancel_pending_orders(self) -> dict:
        """미체결 주문 전체 취소."""
        try:
            orders_resp = self._get(
                "/api/v5/trade/orders-pending",
                params={"instId": config.SYMBOL, "ordType": "limit"}
            )
            orders = orders_resp.get("data", [])
            if not isinstance(orders, list):
                self._log(f"미체결 주문 조회 응답 구조 이상: {type(orders)}", level="ERROR")
                return {"status": "error", "msg": "unexpected response structure"}
            if not orders:
                return {"status": "no_pending_orders"}

            cancel_list = []
            for o in orders:
                if isinstance(o, dict) and "ordId" in o:
                    cancel_list.append({"instId": config.SYMBOL, "ordId": o["ordId"]})
            if not cancel_list:
                return {"status": "no_pending_orders"}

            for i in range(0, len(cancel_list), 20):
                batch = cancel_list[i:i+20]
                self._post("/api/v5/trade/cancel-batch-orders", batch)

            self._log(f"미체결 주문 {len(cancel_list)}개 취소 완료")
            return {"status": "cancelled", "count": len(cancel_list)}
        except Exception as e:
            self._log(f"미체결 주문 취소 실패: {e}", level="ERROR")
            return {"status": "error", "msg": str(e)}

    # ─── OKX API 서명 & 호출 ─────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        try:
            msg    = timestamp + method + path + body
            digest = hmac.new(config.OKX_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
            return base64.b64encode(digest).decode()
        except Exception as e:
            self._log(f"HMAC 서명 실패 (키가 유효하지 않을 수 있음): {e}", level="ERROR")
            raise

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        now = datetime.now(timezone.utc)
        ts  = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        sig = self._sign(ts, method, path, body)
        return {
            "OK-ACCESS-KEY":        config.OKX_API_KEY,
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": config.OKX_PASSPHRASE,
            "Content-Type":         "application/json",
            "Accept":               "application/json",
            "User-Agent":           "snowball-agent/1.0",
            **({"x-simulated-trading": "1"} if config.DEMO_MODE else {}),
        }

    @staticmethod
    def _response_debug(r: httpx.Response) -> str:
        """Short non-sensitive response summary for OKX parse failures."""
        content_type = r.headers.get("content-type", "-")
        text = (r.text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) > 240:
            text = text[:240] + "..."
        if not text:
            text = "<empty>"
        return f"status={r.status_code} content-type={content_type} body={text}"

    def _post(self, path: str, body: dict) -> dict:
        body_str = json.dumps(body)
        last_err = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                headers = self._headers("POST", path, body_str)
                r = self.client.post(path, content=body_str, headers=headers)
                try:
                    return r.json()
                except (json.JSONDecodeError, ValueError) as e:
                    self._log(
                        f"POST {path} JSON 파싱 실패 (시도 {attempt}/{_MAX_RETRIES}): "
                        f"{e} | {self._response_debug(r)}",
                        level="ERROR",
                    )
                    last_err = e
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                self._log(f"POST {path} 네트워크 오류 (시도 {attempt}/{_MAX_RETRIES}): {e}", level="ERROR")
                last_err = e
            except Exception as e:
                self._log(f"POST {path} 실패: {e}", level="ERROR")
                return {"code": "-1", "msg": str(e)}
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
        return {"code": "-1", "msg": f"max retries exceeded: {last_err}"}

    def _get(self, path: str, params: dict = None) -> dict:
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        last_err = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                headers = self._headers("GET", path + query)
                r = self.client.get(path, params=params, headers=headers)
                try:
                    return r.json()
                except (json.JSONDecodeError, ValueError) as e:
                    self._log(
                        f"GET {path} JSON 파싱 실패 (시도 {attempt}/{_MAX_RETRIES}): "
                        f"{e} | {self._response_debug(r)}",
                        level="ERROR",
                    )
                    last_err = e
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                self._log(f"GET {path} 네트워크 오류 (시도 {attempt}/{_MAX_RETRIES}): {e}", level="ERROR")
                last_err = e
            except Exception as e:
                self._log(f"GET {path} 실패: {e}", level="ERROR")
                return {"code": "-1", "msg": str(e)}
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
        return {"code": "-1", "msg": f"max retries exceeded: {last_err}"}

    # ─── 로깅 ────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [{level}] [GridController] {msg}")


# ──────────────────────────────────────────────────────────────
class HedgeController(GridController):
    """무기한 선물(SWAP) 숏 헤지 제어.

    현물 그리드의 재고는 봇에 잠겨 있어 부분 매도가 어렵다. 대신 같은
    기초자산의 USDT 무기한 선물에 숏을 잡아 방향 노출(델타)을 중립화한다.
    그리드는 계속 회전하며 벌고, 가격 하락 손실은 숏이 상쇄한다.

    GridController를 상속해 OKX 서명/HTTP 인프라를 재사용한다.
    """

    def __init__(self, leverage: int = 2):
        super().__init__()
        self.leverage = leverage
        self._ct_val: Optional[float] = None      # 1계약당 기초자산 수량
        self._leverage_set: bool = False

    @property
    def inst_id(self) -> str:
        return f"{config.SYMBOL}-SWAP"   # 예: ETH-USDT → ETH-USDT-SWAP

    # ─── 인스트루먼트 메타 ──────────────────────────────────

    def get_ct_val(self) -> float:
        """1계약당 기초자산 수량 (ETH-USDT-SWAP은 0.1 ETH). 실패 시 0."""
        if self._ct_val:
            return self._ct_val
        try:
            resp = self._get(
                "/api/v5/public/instruments",
                params={"instType": "SWAP", "instId": self.inst_id},
            )
            data = resp.get("data", [])
            if isinstance(data, list) and data:
                self._ct_val = self._safe_float(data[0].get("ctVal"))
                return self._ct_val or 0.0
        except Exception as e:
            self._log(f"계약 단위 조회 실패: {e}", level="ERROR")
        return 0.0

    def _ensure_leverage(self):
        if self._leverage_set:
            return
        resp = self._post("/api/v5/account/set-leverage", {
            "instId": self.inst_id,
            "lever": str(self.leverage),
            "mgnMode": "cross",
        })
        if resp.get("code") == "0":
            self._leverage_set = True
        else:
            self._log(f"레버리지 설정 실패 (계속 진행): {resp.get('msg', resp)}",
                      level="WARNING")

    # ─── 포지션 조회 / 조정 ────────────────────────────────

    def get_short_qty(self) -> float:
        """현재 숏 포지션 크기 (기초자산 단위, 양수). 없으면 0."""
        try:
            resp = self._get("/api/v5/account/positions",
                             params={"instId": self.inst_id})
            data = resp.get("data", [])
            if not isinstance(data, list):
                return 0.0
            ct_val = self.get_ct_val()
            for pos in data:
                if not isinstance(pos, dict):
                    continue
                contracts = self._safe_float(pos.get("pos"))
                if contracts < 0 and ct_val > 0:     # net 모드: 음수 = 숏
                    return abs(contracts) * ct_val
            return 0.0
        except Exception as e:
            self._log(f"헤지 포지션 조회 실패: {e}", level="ERROR")
            return 0.0

    def adjust_short(self, target_qty: float, price: float,
                     min_delta_usd: float = 200.0) -> dict:
        """숏 포지션을 target_qty(기초자산 단위)로 조정.

        현재와 목표의 차이가 min_delta_usd 미만이면 스킵 (수수료 절약).
        Returns: {"status": ..., "current": ..., "target": ..., "delta_qty": ...}
        """
        ct_val = self.get_ct_val()
        if ct_val <= 0:
            return {"status": "no_ct_val"}

        current = self.get_short_qty()
        delta = target_qty - current
        if abs(delta) * price < min_delta_usd:
            return {"status": "skip_small_delta", "current": current,
                    "target": target_qty, "delta_qty": delta}

        contracts = int(abs(delta) / ct_val)
        if contracts <= 0:
            return {"status": "skip_below_lot", "current": current,
                    "target": target_qty, "delta_qty": delta}

        self._ensure_leverage()
        if delta > 0:
            # 숏 확대: sell
            body = {
                "instId": self.inst_id, "tdMode": "cross",
                "side": "sell", "ordType": "market", "sz": str(contracts),
            }
        else:
            # 숏 축소: buy (reduceOnly로 롱 전환 방지)
            body = {
                "instId": self.inst_id, "tdMode": "cross",
                "side": "buy", "ordType": "market", "sz": str(contracts),
                "reduceOnly": "true",
            }

        resp = self._post("/api/v5/trade/order", body)
        if resp.get("code") == "0":
            self._log(
                f"헤지 조정 | 숏 {current:.4f} → {target_qty:.4f} "
                f"({'+' if delta > 0 else ''}{delta:.4f}, {contracts}계약)"
            )
            return {"status": "adjusted", "current": current,
                    "target": target_qty, "delta_qty": delta,
                    "contracts": contracts, "resp": resp}
        self._log(f"헤지 조정 실패: {resp.get('msg', resp)}", level="ERROR")
        return {"status": "error", "resp": resp, "current": current,
                "target": target_qty}

    def close_all(self, price: float) -> dict:
        """헤지 전량 종료."""
        return self.adjust_short(0.0, price, min_delta_usd=0.0)
