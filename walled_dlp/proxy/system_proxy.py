"""Cross-platform OS proxy configuration and mitmproxy CA certificate helpers."""

import logging
import os
from pathlib import Path
import platform
import subprocess
from typing import List, Tuple

logger = logging.getLogger(__name__)


def get_mitmproxy_ca_path() -> Path:
    """Return the default path to mitmproxy's generated local root CA certificate."""
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def _macos_network_services() -> List[str]:
    result = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    services = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("An asterisk") or line.startswith("*"):
            continue
        services.append(line)
    return services


def enable_system_proxy(host: str, port: int) -> Tuple[bool, str]:
    """Configure the OS HTTP/HTTPS proxy to route browser traffic through Walled DLP."""
    system = platform.system()
    try:
        if system == "Darwin":
            services = _macos_network_services()
            if not services:
                return False, "No active macOS network services found."
            for svc in services:
                subprocess.run(
                    ["networksetup", "-setwebproxy", svc, host, str(port)],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["networksetup", "-setsecurewebproxy", svc, host, str(port)],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["networksetup", "-setwebproxystate", svc, "on"],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["networksetup", "-setsecurewebproxystate", svc, "on"],
                    check=False,
                    capture_output=True,
                )
            return True, f"Enabled macOS system proxy ({host}:{port}) on {len(services)} service(s)."

        if system == "Windows":
            import winreg  # type: ignore

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
                winreg.SetValueEx(
                    key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.0.0.1;<local>"
                )
            return True, f"Enabled Windows system proxy ({host}:{port})."

        if system == "Linux":
            if os.environ.get("XDG_CURRENT_DESKTOP", "").lower().find("gnome") != -1:
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
                    check=False,
                )
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy.http", "host", host],
                    check=False,
                )
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)],
                    check=False,
                )
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy.https", "host", host],
                    check=False,
                )
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)],
                    check=False,
                )
                return True, f"Enabled GNOME system proxy ({host}:{port})."
            return (
                False,
                f"Launch your browser with --proxy-server=http://{host}:{port} on this Linux desktop.",
            )
    except Exception as exc:
        logger.exception("Failed to enable system proxy")
        return False, f"Could not enable system proxy: {exc}"

    return False, f"Unsupported OS for automatic system proxy: {system}"


def disable_system_proxy() -> Tuple[bool, str]:
    """Restore OS proxy settings cleanly so normal browsing continues when the app closes."""
    system = platform.system()
    try:
        if system == "Darwin":
            for svc in _macos_network_services():
                subprocess.run(
                    ["networksetup", "-setwebproxystate", svc, "off"],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["networksetup", "-setsecurewebproxystate", svc, "off"],
                    check=False,
                    capture_output=True,
                )
            return True, "Disabled macOS system proxy."

        if system == "Windows":
            import winreg  # type: ignore

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            return True, "Disabled Windows system proxy."

        if system == "Linux":
            if os.environ.get("XDG_CURRENT_DESKTOP", "").lower().find("gnome") != -1:
                subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy", "mode", "none"],
                    check=False,
                )
                return True, "Disabled GNOME system proxy."
            return True, "No system proxy change needed on this Linux environment."
    except Exception as exc:
        logger.exception("Failed to disable system proxy")
        return False, f"Could not disable system proxy: {exc}"

    return False, f"Unsupported OS: {system}"
