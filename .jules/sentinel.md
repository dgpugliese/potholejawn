## 2024-05-18 - IP Spoofing / Rate Limit Bypass in Flask Reverse Proxy setup
**Vulnerability:** The application blindly trusted the `CF-Connecting-IP` header without verifying the `request.remote_addr` is a trusted proxy. Local IPs were exempted from rate limits.
**Learning:** An attacker could spoof `CF-Connecting-IP: 127.0.0.1` and bypass the rate limit completely, leading to API token exhaustion. This is a common pitfall when placing a web app behind a reverse proxy (like Cloudflare Tunnel).
**Prevention:** Only trust the `CF-Connecting-IP` (or `X-Forwarded-For`) header if the immediate upstream client (`request.remote_addr`) is known to be a trusted proxy (e.g., local IPs or a specific proxy IP range). Otherwise, fall back to `request.remote_addr`.
