"""
Sample Security Healthcheck Script (fake/demo)
- Scans a few common ports on a host
- Optionally searches a log file for suspicious indicators

Usage (example):
  python sample_security_healthcheck.py --host 127.0.0.1 --log .\\security.log

Note: This is a demo script with safe defaults and no external dependencies.
"""
from __future__ import annotations
import argparse
import socket
import sys
from pathlib import Path

COMMON_PORTS = [22, 80, 443, 3389]
IOC_KEYWORDS = [
    "Failed password",
    "unauthorized",
    "malware",
    "sql injection",
    "xss",
    "ransom",
]

def check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except Exception:
            return False


def scan_common_ports(host: str) -> dict[int, bool]:
    results: dict[int, bool] = {}
    for p in COMMON_PORTS:
        results[p] = check_port(host, p)
    return results


def search_log_for_iocs(log_path: Path) -> list[str]:
    hits: list[str] = []
    if not log_path.exists():
        return hits
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                lower = line.lower()
                if any(ind.lower() in lower for ind in IOC_KEYWORDS):
                    hits.append(f"Line {i}: {line.strip()}")
    except Exception as e:
        hits.append(f"Error reading log: {e}")
    return hits


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Demo security healthcheck")
    parser.add_argument("--host", default="127.0.0.1", help="Host to scan (default: 127.0.0.1)")
    parser.add_argument("--log", type=str, default=None, help="Optional log file to scan for IOCs")
    args = parser.parse_args(argv)

    print(f"[+] Scanning host: {args.host}")
    results = scan_common_ports(args.host)
    for port, open_ in results.items():
        print(f" - Port {port}: {'OPEN' if open_ else 'closed'}")

    if args.log:
        log_path = Path(args.log)
        print(f"[+] Searching for IOCs in {log_path}")
        hits = search_log_for_iocs(log_path)
        if hits:
            print("[!] Potential indicators found:")
            for h in hits:
                print(f"    - {h}")
        else:
            print("[+] No known indicators found.")

    print("[+] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
