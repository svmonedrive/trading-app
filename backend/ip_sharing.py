"""Detect whether the public IPv4 is dedicated to this connection or shared via ISP CGNAT.

Method 1 (exact): ask the router over UPnP for its WAN IP. WAN IP == public IP -> dedicated;
                  WAN IP private/100.64.0.0/10 -> shared.  (Needs UPnP enabled on the router.)
Method 2 (heuristic): traceroute 2 hops. Hop 1 = your router. If hop 2 (the router's upstream
                  gateway) is a private/CGNAT address, the router's WAN is on a private ISP network,
                  i.e. the public IP is assigned further upstream and shared.
IPv6 addresses are always unique to your connection.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
import urllib.request
from typing import Any

CGNAT_NET = ipaddress.IPv4Network("100.64.0.0/10")


def _is_private_or_cgnat(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or (addr.version == 4 and addr in CGNAT_NET)


# ------------------------------------------------------------------ UPnP
def upnp_wan_ip(timeout: float = 2.5) -> str | None:
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    locations: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, ("239.255.255.250", 1900))
        while True:
            data, _ = sock.recvfrom(4096)
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locations.add(m.group(1).decode())
    except (socket.timeout, OSError):
        pass
    finally:
        sock.close()

    for loc in locations:
        try:
            xml = urllib.request.urlopen(loc, timeout=4).read().decode(errors="replace")
        except Exception:
            continue
        base = re.match(r"(https?://[^/]+)", loc).group(1)
        for svc in re.findall(r"<service>(.*?)</service>", xml, re.S):
            st_m = re.search(r"<serviceType>(.*?)</serviceType>", svc)
            ctrl_m = re.search(r"<controlURL>(.*?)</controlURL>", svc)
            if not st_m or not ctrl_m or not re.search(r"WAN(IP|PPP)Connection", st_m.group(1)):
                continue
            st, ctrl = st_m.group(1), ctrl_m.group(1)
            url = ctrl if ctrl.startswith("http") else base + ("" if ctrl.startswith("/") else "/") + ctrl
            body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:GetExternalIPAddress xmlns:u="{st}"/></s:Body></s:Envelope>')
            req = urllib.request.Request(url, body.encode(), {
                "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{st}#GetExternalIPAddress"'})
            try:
                resp = urllib.request.urlopen(req, timeout=4).read().decode(errors="replace")
                m = re.search(r"<NewExternalIPAddress>\s*([\d.]+)\s*<", resp)
                if m:
                    return m.group(1)
            except Exception:
                continue
    return None


# ------------------------------------------------------------------ traceroute
def first_hops(target: str = "8.8.8.8", hops: int = 2) -> list[str | None]:
    if sys.platform.startswith("win"):
        cmd = ["tracert", "-d", "-h", str(hops), "-w", "800", target]
    else:
        cmd = ["traceroute", "-n", "-m", str(hops), "-w", "1", target]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    result: list[str | None] = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not m:
            continue
        ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", m.group(2))
        result.append(ips[-1] if ips else None)
    return result[:hops]


# ------------------------------------------------------------------ verdict
def detect_sharing(public_ip: str) -> dict[str, Any]:
    addr = ipaddress.ip_address(public_ip)
    if addr.version == 6:
        return {"status": "DEDICATED", "confidence": "high", "method": "ipv6",
                "detail": "IPv6 addresses are unique to your connection.",
                "router_wan_ip": None, "isp_gateway": None}

    wan = None
    try:
        wan = upnp_wan_ip()
    except Exception:
        pass
    if wan:
        if wan == public_ip:
            return {"status": "DEDICATED", "confidence": "high", "method": "upnp",
                    "detail": f"Router WAN IP {wan} equals your public IP - the IP is assigned to your router only.",
                    "router_wan_ip": wan, "isp_gateway": None}
        if _is_private_or_cgnat(wan):
            return {"status": "SHARED", "confidence": "high", "method": "upnp",
                    "detail": f"Router WAN IP {wan} is a private/CGNAT address; your ISP maps it to "
                              f"{public_ip}, which other customers can share.",
                    "router_wan_ip": wan, "isp_gateway": None}

    try:
        hops = first_hops()
    except Exception as exc:
        return {"status": "UNKNOWN", "confidence": "none", "method": "traceroute",
                "detail": f"Could not run traceroute: {exc}", "router_wan_ip": wan, "isp_gateway": None}
    gateway = hops[1] if len(hops) > 1 else None
    if not gateway:
        return {"status": "UNKNOWN", "confidence": "none", "method": "traceroute",
                "detail": "ISP gateway did not answer traceroute; enable UPnP on the router or check its "
                          "status page: if its WAN IPv4 differs from the public IP, it is shared.",
                "router_wan_ip": wan, "isp_gateway": None}
    if _is_private_or_cgnat(gateway):
        return {"status": "SHARED", "confidence": "likely", "method": "traceroute",
                "detail": f"Your router's upstream gateway {gateway} is a private ISP address, so the public IP "
                          f"{public_ip} is assigned further upstream (CGNAT) and is likely shared with other customers.",
                "router_wan_ip": wan, "isp_gateway": gateway}
    return {"status": "DEDICATED", "confidence": "likely", "method": "traceroute",
            "detail": f"Your router's upstream gateway {gateway} is public, so the router most likely holds "
                      f"{public_ip} itself.",
            "router_wan_ip": wan, "isp_gateway": gateway}
