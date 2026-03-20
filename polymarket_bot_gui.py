"""
polymarket_bot_gui.py
─────────────────────
Single-file Polymarket Micro-Edge Bot with full GUI.
All credentials, settings, and controls live inside the app.
No separate scripts needed.

Run:
    python polymarket_bot_gui.py

All required packages are installed automatically on first run.
"""

# ══════════════════════════════════════════════════════════════════
#  AUTO-INSTALLER  (runs before everything else)
#  Checks for required packages and installs any that are missing.
#  The user sees a progress window — no terminal knowledge needed.
# ══════════════════════════════════════════════════════════════════
import sys
import subprocess
import importlib

REQUIRED_PACKAGES = [
    # (import_name,       pip_package_name)
    ("dotenv",           "python-dotenv"),
    ("aiohttp",          "aiohttp"),
    ("web3",             "web3"),
    ("eth_account",      "eth-account"),
    ("py_clob_client",   "py-clob-client"),
]

def _check_and_install():
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name))

    if not missing:
        return   # nothing to do

    # Show a simple console progress indicator first
    # (tkinter may not be up yet at this point)
    print("=" * 56)
    print("  Polymarket Bot — First-Run Setup")
    print("  Installing missing packages automatically...")
    print("=" * 56)

    failed = []
    for import_name, pip_name in missing:
        print(f"  Installing {pip_name}...", end=" ", flush=True)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name,
                 "--quiet", "--disable-pip-version-check"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Verify it actually worked
            importlib.import_module(import_name)
            print("✓")
        except Exception as e:
            print(f"✗  ({e})")
            failed.append(pip_name)

    if failed:
        print("\n  ⚠️  Some packages could not be installed automatically:")
        for pkg in failed:
            print(f"     pip install {pkg}")
        print("\n  Run the above commands manually, then restart the bot.")
        print("  The bot will still start — some features may be limited.")
    else:
        print("\n  ✅  All packages installed. Starting bot...\n")

_check_and_install()   # runs immediately, before any other imports

# ══════════════════════════════════════════════════════════════════
#  IMPORTS
# ══════════════════════════════════════════════════════════════════
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import json
import os
import random
import signal
import logging
import queue
import webbrowser
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
#  CONSTANTS & DEFAULTS
# ══════════════════════════════════════════════════════════════════
SETTINGS_FILE = "bot_settings.json"
POLYMARKET_FEE = 0.002       # 0.2% taker fee per leg
CLOB_HOST_MAIN = "https://clob.polymarket.com"
CLOB_HOST_TEST = "https://clob-staging.polymarket.com"

DEFAULT_SETTINGS = {
    # Credentials
    "private_key":      "",
    "clob_api_key":     "",
    "clob_secret":      "",
    "clob_passphrase":  "",
    "network":          "polygon",
    # Telegram
    "tg_token":         "",
    "tg_chat_id":       "",
    # Trading
    "trade_size":       20.0,
    "min_edge":         0.015,
    "max_spread":       0.05,
    "slippage_buffer":  0.005,
    "order_timeout":    8,
    "loop_sleep":       1.5,
    # Risk
    "max_daily_loss":   100.0,
    "max_open_orders":  10,
    "max_position":     200.0,
    "min_liquidity":    100.0,
    # Mode
    "dry_run":          True,
    # Strategy
    "strategy":         "Safe Arbitrage",
    "risk_profile":     "MEDIUM",
}

# ── Risk presets ─────────────────────────────────────────────────
RISK_PROFILES = {
    "LOW": {
        "trade_size":      10.0,
        "min_edge":        0.020,
        "max_position":    50.0,
        "max_open_orders": 5,
        "max_daily_loss":  50.0,
    },
    "MEDIUM": {
        "trade_size":      20.0,
        "min_edge":        0.015,
        "max_position":    100.0,
        "max_open_orders": 10,
        "max_daily_loss":  100.0,
    },
    "HIGH": {
        "trade_size":      50.0,
        "min_edge":        0.010,
        "max_position":    200.0,
        "max_open_orders": 20,
        "max_daily_loss":  250.0,
    },
}

# ── Strategy descriptions ─────────────────────────────────────────
STRATEGY_INFO = {
    "Safe Arbitrage": (
        "Buys YES and NO together when combined cost < $1.00.\n"
        "Guaranteed profit regardless of outcome. Lowest risk.\n"
        "Best for: beginners, small accounts."
    ),
    "Spread Capture": (
        "Profits from bid/ask spread inefficiencies.\n"
        "Places limit orders at the edge of the spread.\n"
        "Best for: liquid markets with tight pricing."
    ),
    "Multi-Market Arb": (
        "Scans related markets for logical pricing contradictions.\n"
        "e.g. P(A wins championship) cannot exceed P(A makes playoffs).\n"
        "Best for: experienced users, larger market coverage."
    ),
}

# ══════════════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════════════
@dataclass
class MarketSnapshot:
    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    liquidity: float = 0.0       # total USDC depth available

    @property
    def yes_mid(self): return (self.yes_bid + self.yes_ask) / 2
    @property
    def no_mid(self):  return (self.no_bid  + self.no_ask)  / 2
    @property
    def yes_spread(self): return self.yes_ask - self.yes_bid
    @property
    def no_spread(self):  return self.no_ask  - self.no_bid
    @property
    def total_mid(self): return self.yes_mid + self.no_mid


@dataclass
class EdgeSignal:
    strategy:     str
    market_a:     MarketSnapshot
    market_b:     Optional[MarketSnapshot]
    action_a_yes: str
    action_a_no:  str
    action_b_yes: str
    net_edge:     float
    description:  str


@dataclass
class OpenOrder:
    order_id:     str
    market_id:    str
    side:         str
    action:       str
    price:        float
    size_usdc:    float
    submitted_at: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════
#  STRATEGY LEARNER  — lightweight self-tuning engine
#  Tracks edge frequency over time and suggests min_edge adjustments
#  so the bot self-optimises without manual tweaking.
# ══════════════════════════════════════════════════════════════════
class StrategyLearner:
    """
    Records how many edges are found each loop.
    After enough data it compares recent vs older frequency and
    suggests whether to raise or lower min_edge.
    """
    HISTORY_MAX  = 200
    ANALYZE_MIN  = 50    # need at least this many samples to advise
    RECENT_WINDOW = 20
    OLDER_WINDOW  = 50

    def __init__(self):
        self.edge_history: list[int] = []
        self.total_loops  = 0
        self.total_edges  = 0

    def record(self, edge_count: int):
        self.edge_history.append(edge_count)
        self.total_loops += 1
        self.total_edges += edge_count
        if len(self.edge_history) > self.HISTORY_MAX:
            self.edge_history.pop(0)

    def avg_edges_per_loop(self) -> float:
        if not self.total_loops:
            return 0.0
        return self.total_edges / self.total_loops

    def analyze(self) -> Optional[str]:
        """Returns a suggestion string, or None if not enough data yet."""
        if len(self.edge_history) < self.ANALYZE_MIN:
            return None

        recent = sum(self.edge_history[-self.RECENT_WINDOW:])
        older  = sum(self.edge_history[-self.OLDER_WINDOW:-self.RECENT_WINDOW])

        if older == 0:
            return None

        ratio = recent / older

        if ratio > 1.5:
            return ("📈 Edge frequency rising — consider lowering min_edge "
                    "slightly to capture more opportunities")
        if ratio < 0.5:
            return ("📉 Edge frequency falling — consider raising min_edge "
                    "to filter noise and protect quality")
        return None


# ══════════════════════════════════════════════════════════════════
#  BOT ENGINE  (runs in a background thread)
# ══════════════════════════════════════════════════════════════════
class BotEngine:
    """All trading logic. Communicates with GUI via a log_queue."""

    def __init__(self, settings: dict, log_queue: queue.Queue, stats_callback, suggestion_callback=None):
        self.cfg            = settings
        self.log_q          = log_queue
        self.stats_cb       = stats_callback
        self.suggest_cb     = suggestion_callback  # called with suggestion string
        self._running       = False
        self._thread        = None
        self.client         = None

        # State
        self.open_orders: dict[str, OpenOrder] = {}
        self.daily_pnl    = 0.0
        self.trade_count  = 0
        self.loop_count   = 0
        self.learner      = StrategyLearner()

    # ── Logging helper ────────────────────────────────────────────
    def log(self, level: str, msg: str):
        self.log_q.put((level, msg))

    # ── Public control ────────────────────────────────────────────
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    # ── Auth ──────────────────────────────────────────────────────
    def _build_client(self):
        if self.cfg["dry_run"]:
            self.log("INFO", "DRY RUN mode — no real CLOB client created")
            return None
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            host = CLOB_HOST_MAIN if self.cfg["network"] == "polygon" else CLOB_HOST_TEST
            creds = ApiCreds(
                api_key        = self.cfg["clob_api_key"],
                api_secret     = self.cfg["clob_secret"],
                api_passphrase = self.cfg["clob_passphrase"],
            )
            client = ClobClient(
                host           = host,
                key            = self.cfg["private_key"],
                chain_id       = 137 if self.cfg["network"] == "polygon" else 80001,
                creds          = creds,
                signature_type = 2,
            )
            self.log("INFO", f"✅ Authenticated with CLOB  host={host}")
            return client
        except Exception as e:
            self.log("ERROR", f"Auth failed: {e}")
            return None

    # ── Market data ───────────────────────────────────────────────
    def _fetch_markets(self) -> list[dict]:
        if self.cfg["dry_run"]:
            return [
                {
                    "condition_id": f"sim-{i:04d}",
                    "question": f"Will event {chr(65+i%26)}{i} happen before Q3 2026?",
                    "tokens": [
                        {"outcome": "Yes", "token_id": f"yes-{i:04d}"},
                        {"outcome": "No",  "token_id": f"no-{i:04d}"},
                    ],
                }
                for i in range(30)
            ]
        try:
            resp = self.client.get_markets()
            markets = resp.get("data", resp) if isinstance(resp, dict) else resp
            return [m for m in markets if m.get("active") and not m.get("closed")]
        except Exception as e:
            self.log("ERROR", f"fetch_markets: {e}")
            return []

    def _snapshot(self, market: dict) -> Optional[MarketSnapshot]:
        try:
            tokens  = market.get("tokens", [])
            yes_tok = next((t for t in tokens if t["outcome"] == "Yes"), None)
            no_tok  = next((t for t in tokens if t["outcome"] == "No"),  None)
            if not yes_tok or not no_tok:
                return None

            if self.cfg["dry_run"]:
                drift    = random.uniform(-0.04, 0.04)
                yes_mid  = max(0.05, min(0.95, 0.50 + drift))
                no_mid   = 1 - yes_mid + random.uniform(-0.025, 0.025)
                spread   = random.uniform(0.005, 0.015)
                return MarketSnapshot(
                    market_id    = market["condition_id"],
                    question     = market.get("question", "")[:60],
                    yes_token_id = yes_tok["token_id"],
                    no_token_id  = no_tok["token_id"],
                    yes_bid = yes_mid - spread / 2,
                    yes_ask = yes_mid + spread / 2,
                    no_bid  = no_mid  - spread / 2,
                    no_ask  = no_mid  + spread / 2,
                    liquidity = random.uniform(50, 600),
                )

            yes_ob = self.client.get_order_book(yes_tok["token_id"])
            no_ob  = self.client.get_order_book(no_tok["token_id"])

            def best_bid(ob): bids = ob.get("bids",[]); return float(bids[0]["price"]) if bids else 0.0
            def best_ask(ob): asks = ob.get("asks",[]); return float(asks[0]["price"]) if asks else 1.0
            def total_liq(ob):
                return sum(float(x.get("size",0)) for x in ob.get("bids",[])) + \
                       sum(float(x.get("size",0)) for x in ob.get("asks",[]))

            return MarketSnapshot(
                market_id    = market["condition_id"],
                question     = market.get("question","")[:60],
                yes_token_id = yes_tok["token_id"],
                no_token_id  = no_tok["token_id"],
                yes_bid = best_bid(yes_ob), yes_ask = best_ask(yes_ob),
                no_bid  = best_bid(no_ob),  no_ask  = best_ask(no_ob),
                liquidity = total_liq(yes_ob) + total_liq(no_ob),
            )
        except Exception as e:
            self.log("DEBUG", f"snapshot error: {e}")
            return None

    # ── Edge detection ────────────────────────────────────────────
    def _check_rounding_arb(self, s: MarketSnapshot) -> Optional[EdgeSignal]:
        max_spread    = self.cfg["max_spread"]
        min_edge      = self.cfg["min_edge"]
        min_liquidity = self.cfg.get("min_liquidity", 100.0)
        slippage      = self.cfg.get("slippage_buffer", 0.005)

        # Liquidity filter — skip thin markets
        if s.liquidity < min_liquidity:
            return None
        if s.yes_spread > max_spread or s.no_spread > max_spread:
            return None

        buy_cost   = s.yes_ask + s.no_ask
        sell_recv  = s.yes_bid + s.no_bid

        net_buy  = (1.0 - buy_cost)  - 2 * POLYMARKET_FEE - slippage
        net_sell = (sell_recv - 1.0) - 2 * POLYMARKET_FEE - slippage

        if net_buy >= min_edge:
            return EdgeSignal("rounding_arb_buy", s, None,
                "BUY","BUY","SKIP", net_buy,
                f"BUY YES@{s.yes_ask:.4f}+NO@{s.no_ask:.4f}={buy_cost:.4f}<1 "
                f"liq=${s.liquidity:.0f} edge={net_buy:.4f}")
        if net_sell >= min_edge:
            return EdgeSignal("rounding_arb_sell", s, None,
                "SELL","SELL","SKIP", net_sell,
                f"SELL YES@{s.yes_bid:.4f}+NO@{s.no_bid:.4f}={sell_recv:.4f}>1 "
                f"liq=${s.liquidity:.0f} edge={net_sell:.4f}")
        return None

    def _find_related(self, snap: MarketSnapshot, all_snaps: list) -> list:
        STOP = {"will","the","a","of","in","by","be","to","for","is","and","or","on","at","an","it",
                "win","2024","2025","2026","happen","before","after","event"}
        def kw(t): return {w.lower() for w in t.split() if w.lower() not in STOP}
        kw_a = kw(snap.question)
        return [o for o in all_snaps if o.market_id != snap.market_id and len(kw_a & kw(o.question)) >= 3]

    def _check_logical_arb(self, a: MarketSnapshot, b: MarketSnapshot) -> Optional[EdgeSignal]:
        net = (b.yes_ask - a.yes_bid) - 2 * POLYMARKET_FEE
        if net >= self.cfg["min_edge"]:
            return EdgeSignal("logical_arb", a, b, "BUY","SKIP","SELL", net,
                f"BUY '{a.question[:25]}' YES@{a.yes_ask:.4f}  "
                f"SELL '{b.question[:25]}' YES@{b.yes_bid:.4f}  edge={net:.4f}")
        return None

    # ── Order management ──────────────────────────────────────────
    def _place_order(self, market_id, token_id, side, action, price, size_usdc) -> Optional[str]:
        if len(self.open_orders) >= self.cfg["max_open_orders"]:
            self.log("WARN", "MAX_OPEN_ORDERS reached — skipping")
            return None

        if self.cfg["dry_run"]:
            oid = f"DRY-{market_id[:6]}-{side}-{int(time.time()*1000)}"
            self.log("TRADE", f"[DRY] {action} {side} @ {price:.4f}  ${size_usdc:.2f}  {market_id[:16]}")
            self.open_orders[oid] = OpenOrder(oid, market_id, side, action, price, size_usdc)
            self.trade_count += 1
            return oid

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            size_contracts = round(size_usdc / price, 2)
            resp = self.client.create_and_post_order(
                OrderArgs(token_id=token_id, price=price, size=size_contracts,
                          side=action, order_type=OrderType.GTC)
            )
            oid = resp.get("orderID") or resp.get("order_id","unknown")
            self.log("TRADE", f"ORDER {action} {side} @ {price:.4f}  ${size_usdc:.2f}  id={oid}")
            self.open_orders[oid] = OpenOrder(oid, market_id, side, action, price, size_usdc)
            self.trade_count += 1
            return oid
        except Exception as e:
            self.log("ERROR", f"place_order failed: {e}")
            return None

    def _cancel_order(self, oid: str):
        if self.cfg["dry_run"]:
            self.open_orders.pop(oid, None)
            return
        try:
            self.client.cancel(oid)
            self.open_orders.pop(oid, None)
        except Exception as e:
            self.log("WARN", f"Cancel failed {oid}: {e}")

    def _cancel_timed_out(self):
        now = time.time()
        expired = [oid for oid, o in self.open_orders.items()
                   if now - o.submitted_at > self.cfg["order_timeout"]]
        for oid in expired:
            self._cancel_order(oid)
        return len(expired)

    def _cancel_all(self):
        for oid in list(self.open_orders.keys()):
            self._cancel_order(oid)

    # ── Signal execution ──────────────────────────────────────────
    def _execute(self, sig: EdgeSignal):
        s = sig.market_a
        size = self.cfg["trade_size"]

        if sig.strategy.startswith("rounding_arb"):
            if sig.action_a_yes == "BUY":
                r1 = self._place_order(s.market_id, s.yes_token_id, "YES","BUY", s.yes_ask, size)
                r2 = self._place_order(s.market_id, s.no_token_id,  "NO", "BUY", s.no_ask,  size)
            else:
                r1 = self._place_order(s.market_id, s.yes_token_id, "YES","SELL",s.yes_bid, size)
                r2 = self._place_order(s.market_id, s.no_token_id,  "NO", "SELL",s.no_bid,  size)

            # Simulate fill in dry run — both legs placed = profit is locked in
            if self.cfg["dry_run"] and r1 and r2:
                sim_profit = round(sig.net_edge * size, 4)
                self.daily_pnl += sim_profit
                self.log("TRADE",
                    f"[SIM FILL] Both legs filled → "
                    f"+${sim_profit:.4f}  |  Session P&L: ${self.daily_pnl:.4f}")

        elif sig.strategy == "logical_arb" and sig.market_b:
            b = sig.market_b
            r1 = self._place_order(s.market_id, s.yes_token_id, "YES","BUY", s.yes_ask, size)
            r2 = self._place_order(b.market_id, b.yes_token_id, "YES","SELL",b.yes_bid, size)

            if self.cfg["dry_run"] and r1 and r2:
                sim_profit = round(sig.net_edge * size, 4)
                self.daily_pnl += sim_profit
                self.log("TRADE",
                    f"[SIM FILL] Logical arb filled → "
                    f"+${sim_profit:.4f}  |  Session P&L: ${self.daily_pnl:.4f}")

    # ── Telegram ──────────────────────────────────────────────────
    def _telegram(self, msg: str):
        tok = self.cfg.get("tg_token","")
        cid = self.cfg.get("tg_chat_id","")
        if not tok or not cid:
            return
        try:
            url  = f"https://api.telegram.org/bot{tok}/sendMessage"
            data = urllib.parse.urlencode({"chat_id":cid,"text":msg}).encode()
            urllib.request.urlopen(url, data=data, timeout=5)
        except:
            pass

    # ── Main loop ─────────────────────────────────────────────────
    def _loop(self):
        self.log("INFO", "─" * 52)
        self.log("INFO", f"Bot started  DRY_RUN={self.cfg['dry_run']}  "
                          f"MIN_EDGE={self.cfg['min_edge']:.3f}  "
                          f"TRADE_SIZE=${self.cfg['trade_size']:.2f}")
        self.log("INFO", "─" * 52)

        self.client = self._build_client()

        while self._running:
            self.loop_count += 1

            # Daily loss check
            if self.daily_pnl < -abs(self.cfg["max_daily_loss"]):
                msg = f"⛔ DAILY LOSS LIMIT HIT  P&L=${self.daily_pnl:.2f}"
                self.log("ERROR", msg)
                self._telegram(msg)
                self._running = False
                break

            raw_markets = self._fetch_markets()
            if not raw_markets:
                self.log("WARN", "No markets returned — sleeping 10s")
                time.sleep(10)
                continue

            # ── Parallel snapshot building ──────────────────────────
            with ThreadPoolExecutor(max_workers=8) as pool:
                snaps = list(filter(None, pool.map(self._snapshot, raw_markets)))

            # Scan edges — respect chosen strategy
            strategy = self.cfg.get("strategy", "Safe Arbitrage")
            edges: list[EdgeSignal] = []

            if strategy in ("Safe Arbitrage", "Spread Capture"):
                for snap in snaps:
                    sig = self._check_rounding_arb(snap)
                    if sig: edges.append(sig)

            if strategy == "Multi-Market Arb":
                for snap in snaps:
                    for rel in self._find_related(snap, snaps):
                        sig = self._check_logical_arb(snap, rel)
                        if sig: edges.append(sig)

            edges.sort(key=lambda e: e.net_edge, reverse=True)

            # ── Feed learner ────────────────────────────────────────
            self.learner.record(len(edges))
            suggestion = self.learner.analyze()
            if suggestion:
                self.log("INFO", f"🤖 AI Suggestion: {suggestion}")
                if self.suggest_cb:
                    self.suggest_cb(suggestion)

            if edges:
                self.log("INFO", f"Loop #{self.loop_count} — {len(snaps)} markets · "
                                  f"{len(edges)} edge(s)  top={edges[0].net_edge:.4f}  "
                                  f"avg/loop={self.learner.avg_edges_per_loop():.1f}")
                for edge in edges[:3]:   # cap at 3 signals per loop
                    if not self._running: break
                    self.log("EDGE", edge.description)
                    self._execute(edge)
            else:
                self.log("DEBUG", f"Loop #{self.loop_count} — {len(snaps)} markets · no edges")

            # Cancel stale orders
            n = self._cancel_timed_out()
            if n: self.log("INFO", f"Cancelled {n} timed-out orders")

            # Update GUI stats
            self.stats_cb(self.trade_count, self.daily_pnl, len(self.open_orders))

            # Periodic Telegram heartbeat
            if self.loop_count % 120 == 0:
                self._telegram(f"📊 Loop#{self.loop_count} Trades:{self.trade_count} "
                                f"P&L:${self.daily_pnl:.2f}")

            time.sleep(self.cfg["loop_sleep"])

        self._cancel_all()
        self.log("INFO", f"Bot stopped.  Trades: {self.trade_count}  P&L: ${self.daily_pnl:.2f}")
        self._telegram(f"🛑 Bot stopped. Trades:{self.trade_count} P&L:${self.daily_pnl:.2f}")


# ══════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════
class App(tk.Tk):

    # ─── Colour palette ──────────────────────────────────────────
    BG      = "#0d1117"
    PANEL   = "#161b22"
    BORDER  = "#30363d"
    GREEN   = "#3fb950"
    RED     = "#f85149"
    AMBER   = "#d29922"
    BLUE    = "#58a6ff"
    MUTED   = "#8b949e"
    TEXT    = "#e6edf3"
    TRADE   = "#79c0ff"

    FONT_MONO  = ("Courier New", 10)
    FONT_LABEL = ("Segoe UI", 10)
    FONT_BOLD  = ("Segoe UI Semibold", 10)
    FONT_H     = ("Segoe UI Semibold", 13)

    def __init__(self):
        super().__init__()
        self.title("Polymarket Micro-Edge Bot")
        self.geometry("1020x720")
        self.configure(bg=self.BG)
        self.resizable(True, True)

        self.settings  = dict(DEFAULT_SETTINGS)
        self._load_settings()

        self.bot: Optional[BotEngine] = None
        self.log_queue = queue.Queue()
        self._vars     = {}   # holds all tk.StringVar / BooleanVar

        self._build_ui()
        self._poll_logs()

    # ─── Persist settings ─────────────────────────────────────────
    def _load_settings(self):
        if Path(SETTINGS_FILE).exists():
            try:
                with open(SETTINGS_FILE) as f:
                    self.settings.update(json.load(f))
            except:
                pass

    def _save_settings(self):
        safe = {k: v for k, v in self.settings.items() if k != "private_key"}
        # private key saved separately so user consciously opt-in
        safe["private_key"] = self.settings.get("private_key","")
        with open(SETTINGS_FILE, "w") as f:
            json.dump(safe, f, indent=2)

    # ─── UI structure ─────────────────────────────────────────────
    def _build_ui(self):
        # Header bar
        hdr = tk.Frame(self, bg=self.BG, pady=8)
        hdr.pack(fill="x", padx=16)

        # Left: logo title + small signature underneath
        logo_block = tk.Frame(hdr, bg=self.BG)
        logo_block.pack(side="left")
        tk.Label(logo_block, text="⬡  POLYMARKET MICRO-EDGE BOT",
                 bg=self.BG, fg=self.BLUE,
                 font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tk.Label(logo_block, text="by RRR",
                 bg=self.BG, fg="#3d4f63",
                 font=("Segoe UI", 7)).pack(anchor="w", padx=4)

        # Right: Polygon network badge + status
        right_block = tk.Frame(hdr, bg=self.BG)
        right_block.pack(side="right")

        # Polygon badge
        poly_badge = tk.Frame(right_block, bg="#7b3fe4",
                              highlightbackground="#9b5ff4", highlightthickness=1)
        poly_badge.pack(side="left", padx=(0, 14))
        tk.Label(poly_badge, text="⬡ POLYGON",
                 bg="#7b3fe4", fg="#ffffff",
                 font=("Segoe UI Semibold", 8),
                 padx=8, pady=3).pack()

        self._status_lbl = tk.Label(right_block, text="● STOPPED", bg=self.BG,
                                    fg=self.RED, font=self.FONT_BOLD)
        self._status_lbl.pack(side="left")

        # Tab notebook
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("T.TNotebook",           background=self.BG,  borderwidth=0)
        style.configure("T.TNotebook.Tab",       background=self.PANEL, foreground=self.MUTED,
                                                 padding=[14,6], font=self.FONT_LABEL)
        style.map("T.TNotebook.Tab",
                  background=[("selected", self.BG)],
                  foreground=[("selected", self.TEXT)])
        style.configure("T.TFrame",              background=self.BG)

        nb = ttk.Notebook(self, style="T.TNotebook")
        nb.pack(fill="both", expand=True, padx=8, pady=4)

        self._tab_quickstart = self._make_frame(nb)
        self._tab_creds    = self._make_frame(nb)
        self._tab_trading  = self._make_frame(nb)
        self._tab_strategy = self._make_frame(nb)
        self._tab_dash     = self._make_frame(nb)
        self._tab_api      = self._make_frame(nb)

        nb.add(self._tab_quickstart, text="  Quick Start  ")
        nb.add(self._tab_dash,       text="  Dashboard  ")
        nb.add(self._tab_creds,      text="  Credentials  ")
        nb.add(self._tab_trading,    text="  Settings  ")
        nb.add(self._tab_strategy,   text="  Strategy  ")
        nb.add(self._tab_api,        text="  API Guide  ")

        self._build_quickstart(self._tab_quickstart)
        self._build_dashboard(self._tab_dash)
        self._build_credentials(self._tab_creds)
        self._build_trading(self._tab_trading)
        self._build_strategy(self._tab_strategy)
        self._build_api_guide(self._tab_api)

    def _make_frame(self, parent):
        f = ttk.Frame(parent, style="T.TFrame")
        # NOTE: do NOT call f.pack() here — ttk.Notebook manages
        # the geometry of its children via nb.add(). Calling pack()
        # AND nb.add() on the same widget breaks all tab content.
        return f

    # ─── Quick Start tab ─────────────────────────────────────────
    def _build_quickstart(self, parent):
        canvas = tk.Canvas(parent, bg=self.BG, highlightthickness=0)
        scroll = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=self.BG)
        win = canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

        def section(text, color=None):
            tk.Label(inner, text=text, bg=self.BG,
                     fg=color or self.BLUE, font=("Segoe UI Semibold", 12),
                     anchor="w").pack(fill="x", padx=20, pady=(18,4))

        def para(text, color=None, indent=0):
            tk.Label(inner, text=text, bg=self.BG,
                     fg=color or self.TEXT, font=("Segoe UI", 10),
                     justify="left", wraplength=860, anchor="w",
                     padx=20+indent, pady=2).pack(fill="x")

        def divider():
            tk.Frame(inner, bg=self.BORDER, height=1).pack(fill="x", padx=20, pady=8)

        def step_card(num, title, body):
            card = tk.Frame(inner, bg=self.PANEL,
                            highlightbackground=self.BORDER, highlightthickness=1)
            card.pack(fill="x", padx=20, pady=4)
            tk.Label(card, text=f"  {num}", bg=self.BLUE, fg="#000",
                     font=("Segoe UI Semibold", 13), width=3).pack(side="left", fill="y")
            right = tk.Frame(card, bg=self.PANEL, padx=12, pady=8)
            right.pack(side="left", fill="both", expand=True)
            tk.Label(right, text=title, bg=self.PANEL, fg=self.TEXT,
                     font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
            tk.Label(right, text=body, bg=self.PANEL, fg=self.MUTED,
                     font=("Segoe UI", 9), justify="left",
                     wraplength=800, anchor="w").pack(fill="x")

        def profit_table(rows):
            tbl = tk.Frame(inner, bg=self.PANEL,
                           highlightbackground=self.BORDER, highlightthickness=1)
            tbl.pack(fill="x", padx=20, pady=6)
            headers = ["Risk Level", "Est. Monthly Profit", "Balance After 1 Month"]
            cols = [0, 1, 2]
            for c, h in enumerate(headers):
                tk.Label(tbl, text=h, bg=self.BORDER, fg=self.MUTED,
                         font=("Segoe UI Semibold", 9), padx=14, pady=6,
                         anchor="w").grid(row=0, column=c, sticky="ew", padx=1, pady=1)
            for r, (lvl, profit, bal, col) in enumerate(rows, start=1):
                tk.Label(tbl, text=lvl,    bg=self.PANEL, fg=col,
                         font=("Segoe UI Semibold",10), padx=14, pady=6,
                         anchor="w").grid(row=r, column=0, sticky="ew")
                tk.Label(tbl, text=profit, bg=self.PANEL, fg=self.GREEN,
                         font=("Courier New",10), padx=14, pady=6,
                         anchor="w").grid(row=r, column=1, sticky="ew")
                tk.Label(tbl, text=bal,    bg=self.PANEL, fg=self.TEXT,
                         font=("Courier New",10), padx=14, pady=6,
                         anchor="w").grid(row=r, column=2, sticky="ew")
            tbl.columnconfigure(0, weight=1)
            tbl.columnconfigure(1, weight=1)
            tbl.columnconfigure(2, weight=1)

        # ── Welcome ───────────────────────────────────────────────
        tk.Label(inner, text="🚀  WELCOME TO THE POLYMARKET TRADING ASSISTANT",
                 bg=self.BG, fg=self.BLUE, font=("Segoe UI Semibold", 14),
                 anchor="w", padx=20, pady=14).pack(fill="x")

        para("This tool helps you identify pricing inefficiencies in prediction markets "
             "and attempt to profit from them using automated strategies. It is designed "
             "for beginners and emphasises clarity, safety controls, and gradual learning.")
        para("Two key concepts control how the bot behaves:", color=self.AMBER)
        para("STRATEGY  — determines HOW the bot finds profit opportunities.", indent=16)
        para("RISK LEVEL  — determines HOW aggressively it trades.", indent=16)
        para("Both can be changed at any time in the Strategy tab. "
             "All profit examples below assume a starting balance of $100.",
             color=self.MUTED)

        divider()

        # ── Setup steps ───────────────────────────────────────────
        section("📋  HOW TO GET STARTED")
        step_card("1", "Set Up Your Wallet",
                  "Create a dedicated trading wallet (e.g. MetaMask). Fund it with USDC on "
                  "Polygon and a small amount of MATIC for gas (~$1–2).")
        step_card("2", "Enter Your Credentials",
                  "Go to the Credentials tab. Paste your wallet private key, then click "
                  "'Derive API Keys' — the bot fills in the rest automatically.")
        step_card("3", "Choose Your Risk Profile",
                  "Go to the Strategy tab. Select LOW for the safest settings. "
                  "You can always change this later.")
        step_card("4", "Start in Dry Run Mode",
                  "Click ▶ START BOT on the Dashboard. DRY RUN is ON by default — "
                  "the bot scans real markets and shows you exactly what it would trade, "
                  "but never spends real money. Watch the log for a day or two.")
        step_card("5", "Go Live (When Ready)",
                  "Once you trust the system, go to Settings, uncheck DRY RUN, "
                  "then click ▶ START BOT. Start small — you can always scale up.")

        divider()

        # ── Strategy 1 ────────────────────────────────────────────
        section("📊  STRATEGY 1 — SAFE ARBITRAGE", color=self.GREEN)
        para("The most conservative strategy. The bot only trades when both YES and NO "
             "outcomes of a market can be bought together for less than the $1.00 payout.")
        para("Example:  YES costs $0.47 · NO costs $0.50 · Total = $0.97 · Profit = $0.03 guaranteed",
             color=self.TRADE, indent=16)
        para("Trades are less frequent but risk is very low. Best for beginners.",
             color=self.MUTED)
        profit_table([
            ("Low Risk",    "$15 – $40 / month",   "$115 – $140",  self.GREEN),
            ("Medium Risk", "$30 – $80 / month",   "$130 – $180",  self.AMBER),
            ("High Risk",   "$60 – $150 / month",  "$160 – $250",  self.RED),
        ])

        divider()

        # ── Strategy 2 ────────────────────────────────────────────
        section("📊  STRATEGY 2 — SPREAD CAPTURE", color=self.AMBER)
        para("Profits from the gap between the buy price (ask) and sell price (bid) inside "
             "a market's order book. The bot places limit orders between these two prices.")
        para("Example:  Buy at $0.49 · Sell at $0.51 · Spread = $0.02 profit per fill",
             color=self.TRADE, indent=16)
        para("More trades than Safe Arbitrage but moderate exposure to price movement.",
             color=self.MUTED)
        profit_table([
            ("Low Risk",    "$25 – $70 / month",   "$125 – $170",  self.GREEN),
            ("Medium Risk", "$50 – $140 / month",  "$150 – $240",  self.AMBER),
            ("High Risk",   "$100 – $300 / month", "$200 – $400",  self.RED),
        ])

        divider()

        # ── Strategy 3 ────────────────────────────────────────────
        section("📊  STRATEGY 3 — MULTI-MARKET ARBITRAGE", color=self.RED)
        para("Scans related markets for logical pricing contradictions across different "
             "events. When two logically linked markets become mispriced, the bot trades "
             "both sides until prices correct.")
        para("Example:  'Candidate wins election' cannot be cheaper than "
             "'Candidate wins critical state' — those prices must be consistent.",
             color=self.TRADE, indent=16)
        para("Opportunities are rarer but corrections can be larger. "
             "Best for experienced users.", color=self.MUTED)
        profit_table([
            ("Low Risk",    "$30 – $90 / month",   "$130 – $190",  self.GREEN),
            ("Medium Risk", "$70 – $200 / month",  "$170 – $300",  self.AMBER),
            ("High Risk",   "$150 – $400 / month", "$250 – $500",  self.RED),
        ])

        divider()

        # ── Risk levels ───────────────────────────────────────────
        section("🛡️  UNDERSTANDING RISK LEVELS")
        for lvl, col, desc in [
            ("LOW RISK",    self.GREEN,
             "Prioritises safety. Trades only when strong opportunities appear. "
             "Fewer trades, lower volatility, best for starting out."),
            ("MEDIUM RISK", self.AMBER,
             "Balances safety and opportunity. Trades more frequently and accepts "
             "smaller pricing differences. Good after a few days of dry run."),
            ("HIGH RISK",   self.RED,
             "Maximises opportunity detection and frequency. Allows larger exposure "
             "and may produce higher returns — but also higher volatility."),
        ]:
            card = tk.Frame(inner, bg=self.PANEL,
                            highlightbackground=self.BORDER, highlightthickness=1)
            card.pack(fill="x", padx=20, pady=3)
            tk.Label(card, text=f"  {lvl}  ", bg=col,
                     fg="#000", font=("Segoe UI Semibold",10),
                     padx=8, pady=6).pack(side="left", fill="y")
            tk.Label(card, text=desc, bg=self.PANEL, fg=self.TEXT,
                     font=("Segoe UI",9), justify="left",
                     wraplength=820, padx=14, pady=8,
                     anchor="w").pack(side="left", fill="both", expand=True)

        divider()

        # ── Disclaimer ────────────────────────────────────────────
        warn_box = tk.Frame(inner, bg="#2a1a1a",
                            highlightbackground=self.AMBER, highlightthickness=1)
        warn_box.pack(fill="x", padx=20, pady=(4, 6))
        tk.Label(warn_box, text="⚠  IMPORTANT NOTES",
                 bg="#2a1a1a", fg=self.AMBER,
                 font=("Segoe UI Semibold", 11),
                 padx=14, pady=8, anchor="w").pack(fill="x")
        tk.Label(warn_box,
                 text=("Trading in prediction markets involves real financial risk. "
                       "While this tool attempts to identify favourable opportunities, "
                       "profits are never guaranteed.\n\n"
                       "Always start with small amounts until you understand how the "
                       "system behaves. Monitor your positions and adjust risk settings "
                       "to your comfort level."),
                 bg="#2a1a1a", fg=self.AMBER,
                 font=("Segoe UI", 9), justify="left",
                 wraplength=860, padx=14, pady=6,
                 anchor="w").pack(fill="x")
        tk.Frame(warn_box, bg="#2a1a1a", height=6).pack()

        tip = tk.Frame(inner, bg="#1c2a1c",
                       highlightbackground=self.GREEN, highlightthickness=1)
        tip.pack(fill="x", padx=20, pady=(4, 20))
        tk.Label(tip, text="💡  FINAL TIP FOR NEW USERS", bg="#1c2a1c",
                 fg=self.GREEN, font=("Segoe UI Semibold",11),
                 padx=14, pady=8, anchor="w").pack(fill="x")
        tk.Label(tip,
                 text=("Start with:   Safe Arbitrage  ·  Low Risk Mode  ·  Small investment\n\n"
                       "Run in DRY RUN for 1–2 days. Once the log looks correct and you "
                       "understand what the bot is doing, switch to live mode and scale up gradually."),
                 bg="#1c2a1c", fg=self.TEXT, font=("Segoe UI",10),
                 justify="left", wraplength=860, padx=14, pady=6,
                 anchor="w").pack(fill="x")
        tk.Frame(tip, bg="#1c2a1c", height=6).pack()

    # ─── Dashboard tab ────────────────────────────────────────────
    def _build_dashboard(self, parent):
        # Stat tiles
        tiles = tk.Frame(parent, bg=self.BG)
        tiles.pack(fill="x", padx=16, pady=(12,4))

        self._stat_trades = self._stat_tile(tiles, "TRADES", "0")
        self._stat_pnl    = self._stat_tile(tiles, "DAILY P&L", "$0.00")
        self._stat_orders = self._stat_tile(tiles, "OPEN ORDERS", "0")
        self._stat_mode   = self._stat_tile(tiles, "MODE",
                                            "DRY RUN" if self.settings["dry_run"] else "LIVE")

        # AI suggestion banner (hidden until learner fires)
        self._suggestion_frame = tk.Frame(parent, bg="#1c2a1c",
                                          highlightbackground=self.GREEN, highlightthickness=1)
        self._suggestion_lbl = tk.Label(self._suggestion_frame,
                                        text="", bg="#1c2a1c", fg=self.GREEN,
                                        font=("Segoe UI", 9), wraplength=900, justify="left",
                                        padx=10, pady=6)
        self._suggestion_lbl.pack(fill="x")
        # don't pack frame yet — shown when suggestion arrives

        # Quick-links bar
        links_frame = tk.Frame(parent, bg=self.PANEL,
                               highlightbackground=self.BORDER, highlightthickness=1)
        links_frame.pack(fill="x", padx=16, pady=(0,4))
        tk.Label(links_frame, text="Quick Links:", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(10,6), pady=4)
        for label, url in [
            ("Polymarket",      "https://polymarket.com"),
            ("API Docs",        "https://docs.polymarket.com"),
            ("MetaMask",        "https://metamask.io/download"),
            ("Bridge USDC",     "https://wallet.polygon.technology"),
            ("Polygon Faucet",  "https://faucet.polygon.technology"),
            ("Telegram BotFather", "https://t.me/BotFather"),
        ]:
            lbl = tk.Label(links_frame, text=label, bg=self.PANEL, fg=self.BLUE,
                           font=("Segoe UI", 9), cursor="hand2")
            lbl.pack(side="left", padx=6, pady=4)
            lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

        # Control buttons
        ctrl = tk.Frame(parent, bg=self.BG)
        ctrl.pack(fill="x", padx=16, pady=6)

        self._btn_start = self._btn(ctrl, "▶  START BOT", self._start_bot, self.GREEN)
        self._btn_start.pack(side="left", padx=(0,8))
        self._btn_stop  = self._btn(ctrl, "■  STOP",      self._stop_bot,  self.RED)
        self._btn_stop.pack(side="left", padx=(0,8))
        self._btn_stop.config(state="disabled")
        self._btn(ctrl, "💾  Save Settings", self._collect_and_save, self.BLUE).pack(side="right")

        # Log area
        tk.Label(parent, text="LIVE LOG", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI",9)).pack(anchor="w", padx=18, pady=(6,2))

        self._log_box = scrolledtext.ScrolledText(
            parent, bg="#010409", fg=self.TEXT, font=self.FONT_MONO,
            insertbackground=self.TEXT, relief="flat", bd=0,
            wrap="word", state="disabled"
        )
        self._log_box.pack(fill="both", expand=True, padx=16, pady=(0,12))

        # Tag colours for log levels
        self._log_box.tag_config("MUTED", foreground=self.MUTED)   # timestamps
        self._log_box.tag_config("INFO",  foreground=self.TEXT)
        self._log_box.tag_config("DEBUG", foreground=self.MUTED)
        self._log_box.tag_config("EDGE",  foreground=self.GREEN)
        self._log_box.tag_config("TRADE", foreground=self.TRADE)
        self._log_box.tag_config("WARN",  foreground=self.AMBER)
        self._log_box.tag_config("ERROR", foreground=self.RED)

    def _stat_tile(self, parent, label, value):
        f = tk.Frame(parent, bg=self.PANEL, padx=18, pady=10,
                     highlightbackground=self.BORDER, highlightthickness=1)
        f.pack(side="left", expand=True, fill="x", padx=(0,10))
        tk.Label(f, text=label, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI",8)).pack(anchor="w")
        v = tk.Label(f, text=value, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI Semibold",16))
        v.pack(anchor="w")
        return v

    def _btn(self, parent, text, cmd, color):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="#000000" if color in (self.GREEN, self.AMBER) else "#ffffff",
                         font=self.FONT_BOLD, relief="flat", padx=14, pady=6,
                         activebackground=color, cursor="hand2")

    # ─── Credentials tab ─────────────────────────────────────────
    def _build_credentials(self, parent):
        scroll = tk.Frame(parent, bg=self.BG)
        scroll.pack(fill="both", expand=True, padx=24, pady=16)

        self._section(scroll, "🔑  POLYMARKET CLOB CREDENTIALS")
        self._field(scroll, "private_key",     "Private Key (0x…)",  show="*")
        self._field(scroll, "clob_api_key",    "CLOB API Key")
        self._field(scroll, "clob_secret",     "CLOB Secret",        show="*")
        self._field(scroll, "clob_passphrase", "CLOB Passphrase",    show="*")

        # Network radio
        tk.Label(scroll, text="Network", bg=self.BG, fg=self.MUTED,
                 font=self.FONT_LABEL).pack(anchor="w", pady=(10,2))
        net_var = tk.StringVar(value=self.settings["network"])
        self._vars["network"] = net_var
        rf = tk.Frame(scroll, bg=self.BG)
        rf.pack(anchor="w")
        for val, lbl in [("polygon","Polygon Mainnet (real $)"),("mumbai","Mumbai Testnet (fake $)")]:
            tk.Radiobutton(rf, text=lbl, variable=net_var, value=val,
                           bg=self.BG, fg=self.TEXT, selectcolor=self.PANEL,
                           font=self.FONT_LABEL, activebackground=self.BG).pack(side="left", padx=8)

        self._section(scroll, "🔔  TELEGRAM ALERTS  (optional)")
        self._field(scroll, "tg_token",   "Bot Token")
        self._field(scroll, "tg_chat_id", "Chat ID")

        self._section(scroll, "⚙️  DERIVE API KEYS")
        info = ("Run the auth helper below to derive your CLOB API keys from your private key.\n"
                "You only need to do this once. Keys are saved locally in bot_settings.json.")
        tk.Label(scroll, text=info, bg=self.BG, fg=self.MUTED, font=("Segoe UI",9),
                 justify="left", wraplength=700).pack(anchor="w", pady=(4,8))
        self._btn(scroll, "🔐  Derive API Keys Now", self._derive_keys, self.AMBER).pack(anchor="w")

    # ─── Trading settings tab ─────────────────────────────────────
    def _build_trading(self, parent):
        left  = tk.Frame(parent, bg=self.BG)
        right = tk.Frame(parent, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(24,8), pady=16)
        right.pack(side="left", fill="both", expand=True, padx=(8,24), pady=16)

        self._section(left, "📈  TRADING")
        self._field(left, "trade_size",       "Trade Size per leg ($)")
        self._field(left, "min_edge",         "Min Edge (e.g. 0.015 = 1.5%)")
        self._field(left, "max_spread",       "Max Bid-Ask Spread to trade")
        self._field(left, "slippage_buffer",  "Slippage Buffer (e.g. 0.005)")
        self._field(left, "order_timeout",    "Order Timeout (seconds)")
        self._field(left, "loop_sleep",       "Loop Sleep (seconds)")

        self._section(right, "🛡️  RISK CONTROLS")
        self._field(right, "max_daily_loss",   "Max Daily Loss ($)")
        self._field(right, "max_open_orders",  "Max Open Orders")
        self._field(right, "max_position",     "Max Position per Market ($)")
        self._field(right, "min_liquidity",    "Min Market Liquidity ($USDC)")

        self._section(right, "🧪  MODE")
        dry_var = tk.BooleanVar(value=bool(self.settings["dry_run"]))
        self._vars["dry_run"] = dry_var
        cb = tk.Checkbutton(right, text="DRY RUN (no real orders)",
                            variable=dry_var, bg=self.BG, fg=self.TEXT,
                            selectcolor=self.PANEL, font=self.FONT_BOLD,
                            activebackground=self.BG,
                            command=self._update_mode_tile)
        cb.pack(anchor="w", pady=6)
        tk.Label(right, text="Keep enabled until you have verified logs look correct.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI",9)).pack(anchor="w")

    # ─── Strategy tab ─────────────────────────────────────────────
    def _build_strategy(self, parent):
        outer = tk.Frame(parent, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=24, pady=16)

        # ── Risk Profile ──────────────────────────────────────────
        self._section(outer, "⚡  RISK PROFILE  — one click to configure all limits")

        tk.Label(outer,
                 text="Choose a profile to automatically set trade size, edge, position limits and daily loss cap.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9),
                 wraplength=800, justify="left").pack(anchor="w", pady=(0, 8))

        profile_row = tk.Frame(outer, bg=self.BG)
        profile_row.pack(anchor="w", pady=(0, 12))

        self._risk_var = tk.StringVar(value=self.settings.get("risk_profile", "MEDIUM"))

        PROFILE_COLORS = {"LOW": self.GREEN, "MEDIUM": self.AMBER, "HIGH": self.RED}
        PROFILE_DESC   = {
            "LOW":    "Smaller trades · higher edge required · safer",
            "MEDIUM": "Balanced defaults · recommended starting point",
            "HIGH":   "Larger trades · lower edge threshold · more active",
        }

        for profile in ("LOW", "MEDIUM", "HIGH"):
            col = PROFILE_COLORS[profile]
            card = tk.Frame(profile_row, bg=self.PANEL,
                            highlightbackground=self.BORDER, highlightthickness=1,
                            padx=16, pady=10)
            card.pack(side="left", padx=(0, 12))

            rb = tk.Radiobutton(card, text=profile, variable=self._risk_var,
                                value=profile, bg=self.PANEL, fg=col,
                                selectcolor=self.BG, font=("Segoe UI Semibold", 13),
                                activebackground=self.PANEL, cursor="hand2",
                                command=self._apply_risk_profile)
            rb.pack(anchor="w")

            p = RISK_PROFILES[profile]
            detail = (f"Size: ${p['trade_size']:.0f}  "
                      f"Edge: {p['min_edge']*100:.1f}%\n"
                      f"Max pos: ${p['max_position']:.0f}  "
                      f"Daily loss: ${p['max_daily_loss']:.0f}")
            tk.Label(card, text=detail, bg=self.PANEL, fg=self.MUTED,
                     font=("Courier New", 9), justify="left").pack(anchor="w", pady=(4, 0))
            tk.Label(card, text=PROFILE_DESC[profile], bg=self.PANEL, fg=self.TEXT,
                     font=("Segoe UI", 8), wraplength=160, justify="left").pack(anchor="w", pady=(4, 0))

        apply_btn = self._btn(outer, "✓  Apply Selected Profile", self._apply_risk_profile, self.AMBER)
        apply_btn.pack(anchor="w", pady=(0, 20))

        # ── Strategy Selector ─────────────────────────────────────
        self._section(outer, "🎯  STRATEGY SELECTOR")

        tk.Label(outer, text="Choose which arbitrage strategy the bot will use.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))

        self._strat_var = tk.StringVar(value=self.settings.get("strategy", "Safe Arbitrage"))

        strat_frame = tk.Frame(outer, bg=self.BG)
        strat_frame.pack(fill="x")

        self._strat_desc_lbl = tk.Label(outer, text="", bg=self.PANEL, fg=self.TEXT,
                                         font=("Segoe UI", 10), wraplength=800,
                                         justify="left", padx=14, pady=10)
        self._strat_desc_lbl.pack(fill="x", pady=(8, 0))

        for name, desc in STRATEGY_INFO.items():
            row = tk.Frame(strat_frame, bg=self.BG)
            row.pack(fill="x", pady=3)
            rb = tk.Radiobutton(row, text=name, variable=self._strat_var,
                                value=name, bg=self.BG, fg=self.TEXT,
                                selectcolor=self.PANEL, font=self.FONT_BOLD,
                                activebackground=self.BG, cursor="hand2",
                                command=self._update_strat_desc)
            rb.pack(side="left")

        self._update_strat_desc()

    def _apply_risk_profile(self):
        profile = self._risk_var.get()
        overrides = RISK_PROFILES[profile]
        for key, val in overrides.items():
            if key in self._vars:
                self._vars[key].set(str(val))
        self.settings["risk_profile"] = profile
        self._collect_and_save()
        self._append_log("INFO", f"Risk profile set to {profile}  ✓  Settings updated.")

    def _update_strat_desc(self):
        name = self._strat_var.get()
        desc = STRATEGY_INFO.get(name, "")
        self._strat_desc_lbl.config(text=f"ℹ  {desc}")
        self.settings["strategy"] = name

    # ─── API Guide tab ────────────────────────────────────────────
    def _build_api_guide(self, parent):
        t = scrolledtext.ScrolledText(parent, bg=self.PANEL, fg=self.TEXT,
                                      font=("Segoe UI", 10), relief="flat",
                                      bd=0, wrap="word", padx=20, pady=14)
        t.pack(fill="both", expand=True, padx=16, pady=12)

        guide = """
WHAT APIs / ACCOUNTS YOU NEED
══════════════════════════════════════════════════════════════════

① POLYMARKET CLOB API  (required — free)
   ─────────────────────────────────────
   What it is:  The Conditional Limit Order Book that powers all
                Polymarket trading.  REST + WebSocket.

   How to get credentials:
     1. Go to  https://polymarket.com  and connect a wallet
        (MetaMask, Coinbase Wallet, or any EVM wallet works).
     2. Copy your wallet's Private Key  (Settings → Export Private Key).
        ⚠️  Never share this with anyone.  Use a dedicated trading wallet
        with only the funds you plan to trade — not your main wallet.
     3. Click "Derive API Keys" on the Credentials tab above.
        The app will call the CLOB auth endpoint and fill in your keys.
     4. Paste the returned  API Key / Secret / Passphrase  in the
        Credentials fields and click Save Settings.

   Docs:  https://docs.polymarket.com


② POLYGON WALLET WITH USDC  (required)
   ─────────────────────────────────────
   Polymarket settles in USDC on Polygon.  You need:
     • A wallet with USDC on Polygon (not Ethereum mainnet USDC).
     • Small amount of MATIC for gas (~$1–2 worth is plenty).

   How to fund:
     • Bridge USDC via  https://wallet.polygon.technology
       or buy USDC directly on Polygon via Coinbase / Kraken.
     • Deposit into Polymarket at  https://polymarket.com/wallet


③ TELEGRAM BOT  (optional — for alerts)
   ─────────────────────────────────────
   1. Open Telegram, search for  @BotFather.
   2. Send  /newbot  and follow the prompts.
   3. Copy the token it gives you → paste in "Bot Token" field.
   4. To get your Chat ID: message  @userinfobot  in Telegram.
   5. Paste the number in "Chat ID" field.


④ MUMBAI TESTNET  (optional — paper trading)
   ─────────────────────────────────────────
   If you want to test with fake money before going live:
     • Switch "Network" to  Mumbai Testnet  in Credentials tab.
     • Get free test MATIC from  https://faucet.polygon.technology
     • The bot will connect to the staging CLOB instead.


FEES TO EXPECT
══════════════
  • Polymarket taker fee:  ~0.2% per side  (0.4% round-trip)
  • Polygon gas:           fractions of a cent per transaction
  • The MIN_EDGE setting (default 1.5%) already accounts for fees.
    You keep the spread between the edge and the fee.


COST SUMMARY
═════════════════════════════════════════
  API access:        FREE
  Wallet setup:      FREE  (just gas, ~$1–2 MATIC)
  Min trading cap:   $20–$200 USDC to start
  Telegram alerts:   FREE


QUICK-START CHECKLIST
═════════════════════════════════════════
  [ ] Create a dedicated trading wallet (MetaMask)
  [ ] Fund it with USDC on Polygon + small MATIC for gas
  [ ] Paste private key in Credentials tab
  [ ] Click "Derive API Keys" → paste returned keys
  [ ] Keep DRY RUN checked and run the bot for 1–2 days
  [ ] Verify logs show realistic edges and correct order logic
  [ ] Uncheck DRY RUN → click Start Bot → monitor closely
"""
        t.insert("end", guide.strip())
        t.config(state="disabled")

    # ─── Field helper ─────────────────────────────────────────────
    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=self.BG, fg=self.BLUE,
                 font=self.FONT_H).pack(anchor="w", pady=(14,6))

    def _field(self, parent, key, label, show=None):
        tk.Label(parent, text=label, bg=self.BG, fg=self.MUTED,
                 font=self.FONT_LABEL).pack(anchor="w", pady=(4,1))
        var = tk.StringVar(value=str(self.settings.get(key, "")))
        self._vars[key] = var
        kw  = {"show": show} if show else {}
        tk.Entry(parent, textvariable=var, bg=self.PANEL, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat",
                 font=self.FONT_MONO, bd=4, **kw).pack(fill="x", pady=(0,2))

    # ─── Collect settings from all vars ───────────────────────────
    def _collect_settings(self) -> dict:
        s = dict(self.settings)
        float_keys = {"trade_size","min_edge","max_spread","loop_sleep",
                      "max_daily_loss","max_position","slippage_buffer","min_liquidity"}
        int_keys   = {"order_timeout","max_open_orders"}

        for key, var in self._vars.items():
            raw = var.get()
            if key in float_keys:
                try:    s[key] = float(raw)
                except: pass
            elif key in int_keys:
                try:    s[key] = int(raw)
                except: pass
            elif isinstance(var, tk.BooleanVar):
                s[key] = bool(var.get())
            else:
                s[key] = raw
        return s

    def _collect_and_save(self):
        self.settings = self._collect_settings()
        self._save_settings()
        self._append_log("INFO", "Settings saved ✓")
        self._update_mode_tile()

    # ─── Bot control ──────────────────────────────────────────────
    def _start_bot(self):
        self._collect_and_save()
        if not self.settings["dry_run"]:
            if not messagebox.askyesno(
                "⚠️ LIVE MODE",
                "DRY RUN is OFF.\n\nReal orders with real money will be placed.\n\nAre you sure?"
            ):
                return

        self.bot = BotEngine(
            settings           = self.settings,
            log_queue          = self.log_queue,
            stats_callback     = self._on_stats,
            suggestion_callback= self._on_suggestion,
        )
        self.bot.start()
        self._status_lbl.config(text="● RUNNING", fg=self.GREEN)
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")

    def _stop_bot(self):
        if self.bot:
            self.bot.stop()
            self.bot = None
        self._status_lbl.config(text="● STOPPED", fg=self.RED)
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")

    def _derive_keys(self):
        self._collect_and_save()
        pk = self.settings.get("private_key","")
        if not pk:
            messagebox.showerror("Missing Key", "Enter your Private Key first.")
            return
        try:
            from py_clob_client.client import ClobClient
            host = CLOB_HOST_MAIN if self.settings["network"]=="polygon" else CLOB_HOST_TEST
            chain_id = 137 if self.settings["network"]=="polygon" else 80001
            client = ClobClient(host=host, key=pk, chain_id=chain_id)
            creds  = client.create_or_derive_api_creds()
            self._vars["clob_api_key"].set(creds.api_key)
            self._vars["clob_secret"].set(creds.api_secret)
            self._vars["clob_passphrase"].set(creds.api_passphrase)
            self._collect_and_save()
            messagebox.showinfo("✅ Keys Derived", "CLOB API keys derived and saved!")
        except ImportError:
            messagebox.showerror("Missing Package",
                "Install py-clob-client:\n\npip install py-clob-client")
        except Exception as e:
            messagebox.showerror("Derive Failed", str(e))

    # ─── Stats callback (from bot thread) ─────────────────────────
    def _on_stats(self, trades, pnl, open_orders):
        self.after(0, lambda: self._stat_trades.config(text=str(trades)))
        self.after(0, lambda: self._stat_pnl.config(
            text=f"${pnl:.4f}",
            fg=self.GREEN if pnl >= 0 else self.RED))
        self.after(0, lambda: self._stat_orders.config(text=str(open_orders)))

    # ─── AI suggestion callback (from learner) ────────────────────
    def _on_suggestion(self, text: str):
        def _update():
            self._suggestion_lbl.config(text=f"🤖 AI Tuning Tip:  {text}")
            self._suggestion_frame.pack(fill="x", padx=16, pady=(0,4),
                                        before=self._log_box)
        self.after(0, _update)

    def _update_mode_tile(self):
        dry = self._vars.get("dry_run")
        if dry:
            self._stat_mode.config(
                text="DRY RUN" if dry.get() else "🔴 LIVE",
                fg=self.AMBER if dry.get() else self.RED)

    # ─── Log polling ──────────────────────────────────────────────
    def _poll_logs(self):
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                self._append_log(level, msg)
        except queue.Empty:
            pass
        self.after(200, self._poll_logs)

    def _append_log(self, level: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        self._log_box.config(state="normal")
        self._log_box.insert("end", f"[{ts}] ", "MUTED")
        self._log_box.insert("end", f"{msg}\n", level)
        self._log_box.see("end")
        self._log_box.config(state="disabled")
        # keep log manageable
        lines = int(self._log_box.index("end-1c").split(".")[0])
        if lines > 2000:
            self._log_box.config(state="normal")
            self._log_box.delete("1.0","500.0")
            self._log_box.config(state="disabled")

    # ─── Cleanup on close ─────────────────────────────────────────
    def on_close(self):
        if self.bot:
            self.bot.stop()
        self.destroy()


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
