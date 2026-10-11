"""Minimal client for a local Ollama server, using only the standard library.

Security choices:

* **Loopback only by default.** Student essays are personal data. The client
  refuses any host that doesn't resolve to a loopback address unless the caller
  explicitly passes ``allow_remote=True`` (the CLI's ``--allow-remote``).
* **No third-party HTTP stack.** :mod:`urllib` is enough, which keeps the
  supply-chain surface to the Python standard library.

Ollama's native ``/api/chat`` endpoint is used rather than its
OpenAI-compatible endpoint, because the native API exposes everything
reproducibility needs: ``options.seed``, ``options.num_ctx``, ``format``
(JSON-schema constrained output) and ``think`` (gpt-oss reasoning effort).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

#: Ollama's default listen address. 127.0.0.1 is used rather than "localhost" to avoid IPv6/DNS surprises.
DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    """The server is unreachable, returned an error, or the model is missing or unusable."""


def is_loopback(url: str) -> bool:
    """Return True if the URL's host refers only to this machine.

    Literal IP addresses are checked directly. Host names are resolved, and
    *every* address must be loopback, so a name that resolves to both
    127.0.0.1 and a public address is rejected.

    Args:
        url: An ``http(s)://host:port`` URL.
    """
    host = urlparse(url).hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass  # not a literal IP; resolve the name below
    try:
        addrs = {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    # split("%") removes an IPv6 zone id such as "fe80::1%en0".
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs)


class OllamaClient:
    """A small, blocking HTTP client for the Ollama API.

    Args:
        host: Base URL of the Ollama server.
        allow_remote: Allow a host that is not loopback. Off by default.
        timeout: Seconds to wait for one request. Reasoning at "high" effort on a
            long essay can take several minutes, so the default is generous.

    Raises:
        OllamaError: The URL is invalid, or it is remote and ``allow_remote`` is False.
    """

    def __init__(self, host: str = DEFAULT_HOST, *, allow_remote: bool = False, timeout: float = 1800) -> None:
        """Validate the host (http/https, loopback unless ``allow_remote``) before any request is made."""
        if urlparse(host).scheme not in ("http", "https"):
            raise OllamaError(f"invalid Ollama host {host!r}")
        if not allow_remote and not is_loopback(host):
            raise OllamaError(
                f"refusing to send essays to non-local host {host!r}; "
                "pass --allow-remote if you really control that server"
            )
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON request and return the decoded JSON response.

        Raises:
            OllamaError: Any HTTP or network error, with the server's message
                where there is one (truncated to 500 characters).
        """
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.host + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise OllamaError(f"Ollama {path} returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaError(f"cannot reach Ollama at {self.host}: {e}") from e

    def version(self) -> str:
        """Return the Ollama server version string, e.g. ``"0.35.1"``."""
        return self._request("GET", "/api/version").get("version", "unknown")

    def list_models(self) -> list[dict[str, Any]]:
        """Return the installed models as Ollama's ``/api/tags`` reports them (name, digest, details, ...)."""
        return list(self._request("GET", "/api/tags").get("models", []))

    def capabilities(self, model: str) -> list[str] | None:
        """Return a model's capabilities (e.g. ``["completion", "thinking"]``) from ``/api/show``.

        Returns None if they can't be read, for example because Ollama can't
        load that model's metadata. Callers must treat None as "unknown".
        """
        try:
            caps = self._request("POST", "/api/show", {"model": model}).get("capabilities")
        except OllamaError:
            return None
        return [str(c) for c in caps] if isinstance(caps, list) else None

    def model_digest(self, model: str) -> str:
        """Return the content digest of an installed model.

        The digest identifies the exact weights, so a result can be tied to
        them even if the tag (e.g. ``gpt-oss:20b``) is later re-pulled with
        different contents.

        Args:
            model: Model tag. A missing ``:tag`` means ``:latest``.

        Raises:
            OllamaError: The model isn't installed.
        """
        wanted = model if ":" in model else f"{model}:latest"
        for m in self._request("GET", "/api/tags").get("models", []):
            if m.get("name") == wanted or m.get("model") == wanted:
                return m["digest"]
        raise OllamaError(f"model {model!r} is not installed; run: ollama pull {model}")

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None,
        options: dict[str, Any],
        think: str | bool | None,
    ) -> dict[str, Any]:
        """Call ``/api/chat`` without streaming.

        Args:
            model: Model tag.
            messages: Chat messages (``role`` and ``content``).
            schema: JSON schema to constrain the output (Ollama ``format``), or None.
            options: Ollama runtime options such as ``seed``, ``temperature`` and ``num_ctx``.
            think: Reasoning effort for gpt-oss (``"low"``, ``"medium"``, ``"high"``),
                a bool for other reasoning models, or None to leave it unset.

        Returns:
            Ollama's response object. The answer is in ``message.content`` and
            the reasoning trace in ``message.thinking``.
        """
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": options}
        if schema is not None:
            body["format"] = schema
        if think is not None:
            body["think"] = think
        return self._request("POST", "/api/chat", body)
