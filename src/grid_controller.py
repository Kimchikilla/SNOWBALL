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

from config import (
    OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE,
    OKX_BASE_URL, DEMO_MODE,
    SYMBOL, GRID_LOWER, GRID_UPPER, GRID_COUNT, GRID_MODE,
    GRID_BUDGET, ATR_PERIOD
)


class GridController:
    """
    OKX Spot Grid Bot 제어 클래스.

    상태 머신에 따라 아래 액션을 실행합니다:
      NORMAL    → maintain_grid()
      CAUTION   → widen_grid(atr_multiplier=2.0)
      WARNING   → pause_new_orders()
      EMERGENCY → emergency_stop()
    """

    def __init__(self):
        self.bot_id: Optional[str] = None      # 실행 중인 봇 ID
        self.paused: bool = False
        self.client = httpx.Client(base_url=OKX_BASE_URL, timeout=10)

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

    # ─── 공개 액션 메서드 ────────────────────────────────────

    def ensure_grid_running(self, lower=None, upper=None, count=None) -> dict:
        """그리드봇이 없으면 시작, 있으면 그대로."""
        if self.bot_id:
            return {"status": "already_running", "bot_id": self.bot_id}
        return self.start_grid(lower, upper, count)

    def start_grid(self, lower=None, upper=None, count=None) -> dict:
        """새 그리드봇을 시작합니다."""
        lower = lower or GRID_LOWER
        upper = upper or GRID_UPPER
        count = count or GRID_COUNT

        body = {
            "instId":       SYMBOL,
            "algoOrdType":  "grid",
            "maxPx":        str(upper),
            "minPx":        str(lower),
            "gridNum":      str(count),
            "runType":      "1" if GRID_MODE == "arithmetic" else "2",
            "quoteSz":      str(GRID_BUDGET),
        }
        resp = self._post("/api/v5/tradingBot/grid/order-algo", body)

        if resp.get("code") == "0":
            try:
                data = resp.get("data")
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                    self.bot_id = data[0].get("algoId")
                else:
                    self._log(f"그리드봇 시작 응답 구조 이상: {resp}", level="ERROR")
                self.paused = False
                self._log(f"그리드봇 시작 | bot_id={self.bot_id} | 범위={lower}~{upper} | {count}개 그리드")
            except Exception as e:
                self._log(f"그리드봇 시작 응답 파싱 실패: {e}", level="ERROR")
        else:
            self._log(f"그리드봇 시작 실패: {resp}", level="ERROR")

        return resp

    def widen_grid(self, atr_value: float, current_price: float) -> dict:
        """
        CAUTION 상태: 그리드 간격을 ATR x 2배 기준으로 넓힙니다.
        기존 봇을 중지하고 더 넓은 범위로 재시작합니다.
        """
        if not self.bot_id:
            return {"status": "no_bot"}

        wide_range = atr_value * ATR_PERIOD * 2    # 넓힐 폭
        new_lower  = max(current_price - wide_range, GRID_LOWER * 0.8)
        new_upper  = min(current_price + wide_range, GRID_UPPER * 1.2)

        self._log(f"그리드 간격 확대 | 새 범위={new_lower:.0f}~{new_upper:.0f} (ATR={atr_value:.1f})")

        self.stop_grid(sell_remaining=False)
        return self.start_grid(lower=new_lower, upper=new_upper, count=GRID_COUNT)

    def pause_new_orders(self) -> dict:
        """
        WARNING 상태: 신규 주문을 중단하고 기존 체결 포지션만 유지합니다.
        OKX는 그리드봇을 일시 정지하는 직접 API가 없으므로,
        pending 주문을 모두 취소하는 방식으로 구현합니다.
        """
        if self.paused:
            return {"status": "already_paused"}

        resp = self._cancel_pending_orders()
        self.paused = True
        self._log("신규 주문 중단 (WARNING 상태) | 기존 포지션 유지")
        return resp

    def resume_grid(self) -> dict:
        """CAUTION 이하로 복귀 시 그리드 재개."""
        if not self.paused:
            return {"status": "not_paused"}
        self.paused = False
        # 그리드봇 자체는 살아있으므로 재개만 알림
        self._log("그리드 재개 (CAUTION 이하 복귀)")
        return {"status": "resumed"}

    def emergency_stop(self) -> dict:
        """
        EMERGENCY 상태: 모든 포지션을 시장가로 즉시 청산합니다.
        """
        self._log("⚠️ 긴급 청산 실행 (EMERGENCY)", level="CRITICAL")
        result = self.stop_grid(sell_remaining=True)     # sell_remaining=True → 보유 코인 시장가 매도
        self.bot_id = None
        self.paused = False
        return result

    def stop_grid(self, sell_remaining: bool = False) -> dict:
        """그리드봇을 중지합니다."""
        if not self.bot_id:
            return {"status": "no_bot"}

        body = {
            "algoId":    self.bot_id,
            "instId":    SYMBOL,
            "algoOrdType": "grid",
        }
        # stopType: "1" = 기존 포지션 유지, "2" = 시장가 청산
        body["stopType"] = "2" if sell_remaining else "1"

        resp = self._post("/api/v5/tradingBot/grid/stop-order-algo", body)

        if resp.get("code") == "0":
            self._log(f"그리드봇 중지 | sell_remaining={sell_remaining}")
            self.bot_id = None
        else:
            self._log(f"그리드봇 중지 실패: {resp}", level="ERROR")

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
                params={"instId": SYMBOL, "limit": str(limit)}
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

    # ─── 주문 관리 ───────────────────────────────────────────

    def _cancel_pending_orders(self) -> dict:
        """미체결 주문 전체 취소."""
        try:
            orders_resp = self._get(
                "/api/v5/trade/orders-pending",
                params={"instId": SYMBOL, "ordType": "limit"}
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
                    cancel_list.append({"instId": SYMBOL, "ordId": o["ordId"]})
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
            digest = hmac.new(OKX_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
            return base64.b64encode(digest).decode()
        except Exception as e:
            self._log(f"HMAC 서명 실패 (키가 유효하지 않을 수 있음): {e}", level="ERROR")
            raise

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        sig = self._sign(ts, method, path, body)
        return {
            "OK-ACCESS-KEY":        OKX_API_KEY,
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
            "Content-Type":         "application/json",
            **({"x-simulated-trading": "1"} if DEMO_MODE else {}),
        }

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
                    self._log(f"POST {path} JSON 파싱 실패 (시도 {attempt}/{_MAX_RETRIES}): {e}", level="ERROR")
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
                    self._log(f"GET {path} JSON 파싱 실패 (시도 {attempt}/{_MAX_RETRIES}): {e}", level="ERROR")
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
