import ipaddress
import socket
from urllib.parse import urlparse
from typing import Tuple

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local & cloud metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),        # IPv6 Unique Local
    ipaddress.ip_network("fe80::/10"),       # IPv6 Link-Local
]


def validate_webhook_url(url: str) -> Tuple[bool, str]:
    """
    Validate a webhook URL against SSRF attacks.
    Returns (is_valid, error_reason).
    """
    if not url:
        return False, "URL cannot be empty."

    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL structure: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid URL scheme '{parsed.scheme}'. Only 'http' and 'https' are permitted."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL must include a valid hostname."

    # Check for localhost / loopback aliases
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "metadata.google.internal"):
        return False, "Targeting localhost or cloud internal metadata endpoints is prohibited (SSRF protection)."

    # Resolve IP address to check against private subnets
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        ip_addresses = {info[4][0] for info in addr_info}
    except socket.gaierror:
        return False, f"Could not resolve hostname '{hostname}'."
    except Exception as e:
        return False, f"DNS resolution error: {e}"

    for ip_str in ip_addresses:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            for blocked_net in BLOCKED_IP_NETWORKS:
                if ip_obj in blocked_net:
                    return False, f"Destination IP {ip_str} falls within a restricted/private network range ({blocked_net})."
        except ValueError:
            return False, f"Invalid IP address format: {ip_str}"

    return True, ""
