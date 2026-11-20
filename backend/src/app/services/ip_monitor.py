"""Public IP monitor (IPv4 or IPv6).

Zerodha only accepts API orders from the IP whitelisted at developers.kite.trade.
api.kite.trade is reachable over both IPv4 and IPv6, so the IP Zerodha sees is whichever
protocol this PC prefers. We therefore look the IP up through a dual-stack service
(api64.ipify.org) using the same network stack Python uses to call Kite.

Any mismatch, lookup failure, protocol switch (IPv4 <-> IPv6), or disagreement between two
lookup services blocks order placement until the user confirms they updated Zerodha.
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from typing import Any

import requests

from app.core.config import ConfigStore, Settings, now_ist
from app.core.db import TradeDB
from app.services.ip_sharing import detect_sharing

SHARING_RECHECK_SECONDS = 30 * 60
MAX_HISTORY = 20


def _norm(ip: str) -> str:
    return str(ipaddress.ip_address(ip.strip()))


# Zerodha's exact wording is not documented; match the obvious variants ("IP", "whitelist", "static IP"...).
_IP_REJECTION_RE = re.compile(r"\bip\b|\bip[- ]?address|white[- ]?list|static[- ]?ip|\bipv[46]\b", re.IGNORECASE)


def looks_like_ip_rejection(message: str | None) -> bool:
    return bool(message and _IP_REJECTION_RE.search(message))


def _version(ip: str | None) -> int | None:
    try:
        return ipaddress.ip_address(ip).version if ip else None
    except ValueError:
        return None


class IPMonitor:
    def __init__(self, settings: Settings, store: ConfigStore, db: TradeDB):
        self._settings = settings
        self._store = store
        self._db = db
        self._lock = threading.Lock()
        self.current_ip: str | None = None
        self.secondary_ip: str | None = None
        self.last_checked: str | None = None
        self.last_error: str | None = None
        self.sharing: dict[str, Any] | None = None
        self._sharing_checked_for: str | None = None
        self._sharing_checked_at = 0.0
        self._was_matched: bool | None = None
        # migrate the old IPv6-only key
        if store.get("whitelisted_ip") is None and store.get("whitelisted_ipv6"):
            store.set(whitelisted_ip=store.get("whitelisted_ipv6"))
        # backfill the confirm time for whitelists saved before whitelisted_at existed
        wl = store.get("whitelisted_ip")
        if wl and not store.get("whitelisted_at"):
            at = db.last_event_time(f"Whitelisted IP updated to {wl} ")
            if at:
                store.set(whitelisted_at=at)

    def _lookup(self, url: str) -> str:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        text = resp.text.strip()
        if text.startswith("{"):
            text = resp.json()["ip"]
        return _norm(text)

    def check(self) -> dict[str, Any]:
        ip = secondary = error = None
        try:
            ip = self._lookup(self._settings.ip_lookup_url)
        except requests.ConnectionError as exc:
            error = f"Public IP lookup failed - no internet connection? ({exc.__class__.__name__})"
        except Exception as exc:
            error = f"Public IP lookup failed: {exc}"

        if ip and self._settings.ip_secondary_lookup_url:
            try:
                secondary = self._lookup(self._settings.ip_secondary_lookup_url)
            except Exception:
                secondary = None  # secondary is advisory only
            if secondary and _version(secondary) == _version(ip) and secondary != ip:
                error = (f"Two IP services disagree ({ip} vs {secondary}). Your ISP may be rotating the "
                         f"shared public IP per connection - orders could reach Zerodha from either IP.")

        with self._lock:
            self.current_ip = ip
            self.secondary_ip = secondary
            self.last_error = error
            self.last_checked = now_ist().isoformat(timespec="seconds")
            if ip:
                self._record_if_changed(ip)

        if ip and (ip != self._sharing_checked_for
                   or time.monotonic() - self._sharing_checked_at > SHARING_RECHECK_SECONDS):
            self._refresh_sharing(ip)

        matched = self.is_matched()
        if self._was_matched is not None and matched != self._was_matched:
            if matched:
                self._db.log("INFO", f"Public IP verified again ({ip}). Order execution unlocked.")
            else:
                self._db.log("WARN", f"Public IP CHANGED/UNVERIFIED: live={ip or 'unavailable'} "
                                     f"whitelisted={self._store.get('whitelisted_ip')}. Orders paused."
                                     + (f" {error}" if error else ""))
        elif self._was_matched is None and not matched:
            self._db.log("WARN", "Public IP not verified at startup. Orders blocked until it matches the whitelist.")
        self._was_matched = matched
        return self.status()

    def _record_if_changed(self, ip: str) -> None:
        """Persist every public-IP change (also catches changes that happened while the app was off).
        Failed lookups are not changes - we only compare successful readings."""
        last_seen = self._store.get("last_seen_ip")
        if last_seen == ip:
            return
        if last_seen:
            change = {"from_ip": last_seen, "to_ip": ip, "at": now_ist().isoformat(timespec="seconds")}
            history = [change, *self._store.get("ip_changes", [])][:MAX_HISTORY]
            self._store.set(last_seen_ip=ip, ip_changes=history)
            self._db.log("WARN", f"PUBLIC IP CHANGED: {last_seen} -> {ip}")
        else:
            self._store.set(last_seen_ip=ip)

    def _refresh_sharing(self, ip: str) -> None:
        try:
            result = detect_sharing(ip)
        except Exception as exc:
            result = {"status": "UNKNOWN", "confidence": "none", "method": "error",
                      "detail": f"Sharing check failed: {exc}", "router_wan_ip": None, "isp_gateway": None}
        result["checked_at"] = now_ist().isoformat(timespec="seconds")
        changed = (self.sharing or {}).get("status") != result["status"]
        self.sharing = result
        self._sharing_checked_for = ip
        self._sharing_checked_at = time.monotonic()
        if changed:
            self._db.log("WARN" if result["status"] == "SHARED" else "INFO",
                         f"IP {ip} sharing check: {result['status']} ({result['confidence']}) - {result['detail']}")

    # ---------- feedback from Zerodha ----------
    def report_zerodha_rejection(self, message: str, order_ref: str) -> bool:
        """Called with every order error/rejection text. If Zerodha blamed the IP, distrust the saved
        whitelist until the user re-confirms. Returns True if it was an IP rejection."""
        if not looks_like_ip_rejection(message):
            return False
        rejection = {"ip": self.current_ip or self._store.get("last_seen_ip"),
                     "whitelisted_ip": self._store.get("whitelisted_ip"),
                     "message": message, "order_ref": order_ref,
                     "at": now_ist().isoformat(timespec="seconds")}
        self._store.set(zerodha_ip_rejection=rejection)
        self._was_matched = False
        self._db.log("ERROR", f"ZERODHA REJECTED THE IP {rejection['ip']} (order {order_ref}): {message.rstrip('. ')}. "
                              f"Orders blocked until you re-whitelist and confirm.")
        return True

    def is_matched(self) -> bool:
        wl = self._store.get("whitelisted_ip")
        if not self.current_ip or not wl or self.last_error:
            return False
        if self._store.get("zerodha_ip_rejection"):
            return False  # Zerodha itself said this IP is not allowed
        try:
            return ipaddress.ip_address(self.current_ip) == ipaddress.ip_address(wl)
        except ValueError:
            return False

    def status(self) -> dict[str, Any]:
        wl = self._store.get("whitelisted_ip")
        cur_v, wl_v = _version(self.current_ip), _version(wl)
        changes = self._store.get("ip_changes", [])
        last = changes[0] if changes else None
        return {
            "previous_ip": last["from_ip"] if last else None,
            "changed_at": last["at"] if last else None,
            "ip_changes": changes[:5],
            "check_interval_seconds": self._settings.ip_check_interval_seconds,
            "current_ip": self.current_ip,
            "ip_version": cur_v,
            "whitelisted_ip": wl,
            "whitelisted_version": wl_v,
            "whitelisted_at": self._store.get("whitelisted_at"),
            "protocol_changed": bool(cur_v and wl_v and cur_v != wl_v),
            "is_matched": self.is_matched(),
            "last_checked": self.last_checked,
            "error": self.last_error,
            "sharing": self.sharing,
            "zerodha_rejection": self._store.get("zerodha_ip_rejection"),
        }

    def confirm(self) -> dict[str, Any]:
        """User confirmed the current public IP is now whitelisted on developers.kite.trade."""
        self.check()  # whitelist the IP that is live *right now*
        if not self.current_ip:
            raise RuntimeError(self.last_error or "Current public IP unknown")
        if self.last_error:
            raise RuntimeError(self.last_error)
        self._store.set(whitelisted_ip=self.current_ip, zerodha_ip_rejection=None,
                        whitelisted_at=now_ist().isoformat(timespec="seconds"))
        self._was_matched = True
        self._db.log("INFO", f"Whitelisted IP updated to {self.current_ip} (IPv{_version(self.current_ip)}) "
                             f"by user. Orders unlocked.")
        return self.status()
