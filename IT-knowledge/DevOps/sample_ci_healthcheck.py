"""
Sample CI Healthcheck Script (fake/demo)
- Verifies presence of common tooling
- Checks expected CI environment variables

Usage (example):
  python sample_ci_healthcheck.py
"""
from __future__ import annotations
import shutil
import subprocess
import sys
import os
from typing import Iterable

TOOLS = ["git", "dotnet", "python"]
ENV_VARS = ["BUILD_NUMBER", "GIT_COMMIT", "AZURE_SUBSCRIPTION_ID"]


def is_tool_available(tool: str) -> bool:
    return shutil.which(tool) is not None


def check_tools(tools: Iterable[str]) -> dict[str, bool]:
    return {t: is_tool_available(t) for t in tools}


def check_env_vars(vars: Iterable[str]) -> dict[str, bool]:
    return {v: (os.getenv(v) is not None) for v in vars}


def get_version(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=5)
        return out.strip().splitlines()[0]
    except Exception as e:
        return f"unknown ({e})"


def main(argv: list[str]) -> int:
    print("[+] Checking required tools...")
    tool_status = check_tools(TOOLS)
    for t, ok in tool_status.items():
        print(f" - {t}: {'found' if ok else 'missing'}")
    if tool_status.get("git"):
        print(f"    git version: {get_version(['git', '--version'])}")
    if tool_status.get("dotnet"):
        print(f"    dotnet version: {get_version(['dotnet', '--version'])}")
    if tool_status.get("python"):
        print(f"    python version: {get_version([sys.executable, '--version'])}")

    print("[+] Checking CI environment variables...")
    env_status = check_env_vars(ENV_VARS)
    for v, ok in env_status.items():
        print(f" - {v}: {'present' if ok else 'absent'}")

    missing_tools = [t for t, ok in tool_status.items() if not ok]
    missing_env = [v for v, ok in env_status.items() if not ok]

    if missing_tools or missing_env:
        print("[!] CI healthcheck FAILED")
        if missing_tools:
            print("    Missing tools:", ", ".join(missing_tools))
        if missing_env:
            print("    Missing env vars:", ", ".join(missing_env))
        return 1

    print("[+] CI healthcheck PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
