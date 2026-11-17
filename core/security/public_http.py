"""HTTPS transport pins each connection to a verified public DNS address.

Preserves the original Host header and TLS server name. A second DNS lookup in
the HTTP connection cannot redirect an operator-configured URL to private IPs.
"""
import ipaddress
import socket

import httpx


class PublicTransport(httpx.BaseTransport):
    def __init__(self, *, resolver=None, transport=None, http_module=httpx):
        self.http = http_module
        self.resolver = resolver or socket.getaddrinfo
        self.transport = transport or self.http.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        port = 443 if url.port is None else url.port
        if url.scheme != 'https' or not url.host or not 1 <= port <= 65535 or url.userinfo or url.fragment:
            raise ValueError('Public HTTPS endpoint required')
        rows = self.resolver(url.host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses or len(addresses) > 32:
            raise ValueError('Bounded public DNS response required')
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError('Private or special network destinations are forbidden')
        headers = self.http.Headers(request.headers)
        headers['Host'] = url.netloc.decode('ascii')
        pinned = self.http.Request(request.method, url.copy_with(host=addresses[0]),
            headers=headers, stream=request.stream,
            extensions={**request.extensions, 'sni_hostname': url.host})
        response = self.transport.handle_request(pinned)
        if 300 <= response.status_code < 400:
            response.close()
            raise ValueError('Redirected credential requests are forbidden')
        return response

    def close(self) -> None:
        self.transport.close()
