import os
import requests
import base64
import time
import logging
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)


class TulipApiClient:
    """HTTP client for Tulip API with retry logic, Basic Auth, and exponential backoff."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        workspace_id: str = "",
        max_retries: int = 3,
        base_delay: int = 1000,
        max_delay: int = 30000,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

        # Create credentials
        credentials = base64.b64encode(
            f"{api_key}:{api_secret}".encode()
        ).decode()
        self.auth_header = f"Basic {credentials}"

        # Determine API key type
        self.is_workspace_api_key = not workspace_id
        self.is_account_api_key = bool(workspace_id)

    def _is_next_gen_api(self, endpoint: str) -> bool:
        """Check if this is a next-gen API endpoint (stations or users)."""
        return endpoint.startswith("/api/stations/v1/") or endpoint.startswith(
            "/api/users/v1/"
        )

    def _build_url(self, endpoint: str) -> str:
        """Build full URL based on API key type and endpoint type."""
        if self._is_next_gen_api(endpoint):
            # Next-gen APIs: workspace ID may be embedded in endpoint path
            if self.is_account_api_key:
                # Account API key: /api/stations/v1/w/{workspaceId}...
                # Insert workspace ID after /api/stations/v1/ or /api/users/v1/
                if endpoint.startswith("/api/stations/v1/"):
                    endpoint = f"/api/stations/v1/w/{self.workspace_id}{endpoint[17:]}"
                elif endpoint.startswith("/api/users/v1/"):
                    endpoint = f"/api/users/v1/w/{self.workspace_id}{endpoint[14:]}"
            return f"{self.base_url}{endpoint}"
        else:
            # Legacy APIs: /api/v3/...
            if self.is_workspace_api_key:
                # Workspace API Key
                return f"{self.base_url}/api/v3{endpoint}"
            else:
                # Account API Key
                return f"{self.base_url}/api/v3/w/{self.workspace_id}{endpoint}"

    def _is_retryable_status(self, status_code: int) -> bool:
        """Check if HTTP status code warrants a retry."""
        return status_code == 429 or status_code >= 500

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if network error warrants a retry."""
        retryable_errors = [
            "ECONNRESET",
            "ENOTFOUND",
            "ECONNREFUSED",
            "ETIMEDOUT",
            "ENETUNREACH",
        ]
        error_str = str(error).upper()
        return any(err in error_str for err in retryable_errors)

    def _parse_retry_after(self, retry_after_str: str) -> Optional[int]:
        """Parse Retry-After header (expected to be seconds as integer or HTTP-date)."""
        try:
            return int(retry_after_str)
        except ValueError:
            # Could be HTTP-date, but for MVP just treat as unparseable
            return None

    def _calculate_backoff_delay(self, attempt: int, retry_after: Optional[int] = None) -> int:
        """Calculate delay in milliseconds using exponential backoff with jitter."""
        import random

        if retry_after is not None:
            # Use Retry-After header if valid and within max delay
            delay_ms = retry_after * 1000
            if delay_ms <= self.max_delay:
                return delay_ms

        # Exponential backoff: base_delay * 2^attempt
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)

        # Add jitter: 0-10% of delay
        jitter = random.random() * 0.1 * delay
        return int(delay + jitter)

    def make_request(
        self,
        endpoint: str,
        method: str = "GET",
        body: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        Make HTTP request to Tulip API with retry logic.

        Args:
            endpoint: API endpoint (e.g., "/tables")
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            body: Request body for POST/PUT/PATCH
            query_params: Query string parameters
            custom_headers: Additional headers to include

        Returns:
            Parsed response (JSON or text depending on content-type)

        Raises:
            requests.HTTPError: For non-retryable HTTP errors
            Exception: For unrecoverable network errors
        """
        url = self._build_url(endpoint)

        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
        }
        if custom_headers:
            headers.update(custom_headers)

        attempt = 0
        while attempt <= self.max_retries:
            try:
                kwargs = {
                    "headers": headers,
                    "timeout": (10, 60),  # (connect_timeout, read_timeout)
                }

                if query_params:
                    kwargs["params"] = query_params

                if body and method in ["POST", "PUT", "PATCH"]:
                    kwargs["json"] = body

                logger.debug(
                    f"{method} {url} (attempt {attempt + 1}/{self.max_retries + 1})"
                )
                
                cert_path = os.getenv("ZSCALER_CERT_PATH")
                verify = cert_path if cert_path else True

                response = requests.request(method, url, verify=verify, **kwargs)

                # Handle successful responses
                if response.ok:
                    # Parse response based on content-type
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        return response.json()
                    else:
                        return response.text()

                # Handle client errors (non-retryable)
                if not self._is_retryable_status(response.status_code):
                    logger.error(
                        f"Non-retryable HTTP {response.status_code}: {response.text}"
                    )
                    response.raise_for_status()

                # Handle retryable server/rate-limit errors
                retry_after = None
                if response.status_code == 429:
                    retry_after_header = response.headers.get("retry-after")
                    if retry_after_header:
                        retry_after = self._parse_retry_after(retry_after_header)

                if attempt >= self.max_retries:
                    logger.error(f"Max retries exceeded for {method} {url}")
                    response.raise_for_status()

                delay_ms = self._calculate_backoff_delay(attempt, retry_after)
                delay_sec = delay_ms / 1000
                logger.warning(
                    f"Retryable error HTTP {response.status_code}, retrying in {delay_sec:.2f}s"
                )
                time.sleep(delay_sec)
                attempt += 1

            except requests.exceptions.RequestException as e:
                # Network errors
                if self._is_retryable_error(e) and attempt < self.max_retries:
                    delay_ms = self._calculate_backoff_delay(attempt)
                    delay_sec = delay_ms / 1000
                    logger.warning(
                        f"Retryable network error: {e}, retrying in {delay_sec:.2f}s"
                    )
                    time.sleep(delay_sec)
                    attempt += 1
                else:
                    logger.error(f"Non-retryable error: {e}")
                    raise

        # Should not reach here
        raise Exception(f"Unexpected error after {self.max_retries} retries")
