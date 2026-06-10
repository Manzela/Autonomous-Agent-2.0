"""Acceptance eval — network egress / SSRF protection (tools/url_safety.py).

Asserts the README's "network egress allowlist — agent cannot exfiltrate data to
arbitrary endpoints" safety control: cloud metadata/credential endpoints and
private/internal addresses are refused, and non-HTTP schemes are rejected.
Hermetic (literal IPs + hardcoded metadata hostnames; no DNS).
"""
from __future__ import annotations

import pytest

from tools.url_safety import is_always_blocked_url, is_safe_url


# Cloud metadata / credential endpoints — must be blocked UNCONDITIONALLY
# (even if security.allow_private_urls is toggled on), since these hand out
# instance IAM credentials and are the classic SSRF exfil target.
METADATA_ENDPOINTS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",  # AWS/GCP/Azure
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    "http://169.254.170.2/v2/credentials/",  # AWS ECS task IAM creds
    "http://100.100.100.200/latest/meta-data/",  # Alibaba Cloud
]


@pytest.mark.parametrize("url", METADATA_ENDPOINTS)
def test_cloud_metadata_always_blocked(url: str) -> None:
    assert is_always_blocked_url(url) is True, f"metadata endpoint not in always-blocked floor: {url}"
    assert is_safe_url(url) is False


# Private / loopback / link-local addresses — SSRF into internal services.
PRIVATE_ADDRESSES = [
    "http://127.0.0.1/admin",
    "http://10.0.0.5/internal",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
]


@pytest.mark.parametrize("url", PRIVATE_ADDRESSES)
def test_private_internal_addresses_blocked(url: str) -> None:
    assert is_safe_url(url) is False, f"private/internal address was allowed: {url}"


# Non-HTTP schemes — file/ftp/gopher are classic SSRF/local-file escalators.
BAD_SCHEMES = ["file:///etc/passwd", "ftp://internal-host/secrets", "gopher://host:70/x"]


@pytest.mark.parametrize("url", BAD_SCHEMES)
def test_non_http_schemes_blocked(url: str) -> None:
    assert is_safe_url(url) is False, f"unsupported scheme was allowed: {url}"
