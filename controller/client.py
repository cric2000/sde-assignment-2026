"""HTTP boundary. Never blindly retry side effects."""

import time

import httpx


class APIError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"HTTP {status}: {body}")


class API:
    def __init__(self, url, transport=None):
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=180, transport=transport,
                                 trust_env=False)

    def close(self):
        self.http.close()

    def request(self, method, path, **kwargs):
        # Only reads have automatic transport/server retries. Mutations reconcile first.
        attempts = 4 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.http.request(method, path, **kwargs)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if response.is_error:
                    try:
                        body = response.json()
                    except ValueError:
                        body = response.text[:1000]
                    raise APIError(response.status_code, body)
                return response.json()
            except httpx.TransportError:
                if attempt + 1 == attempts:
                    raise
                time.sleep(min(2 ** attempt, 8))

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def post(self, path, body):
        return self.request("POST", path, json=body)

    def put(self, path, body):
        return self.request("PUT", path, json=body)

    def lines(self, chunk):
        # Partial stream failure is recovered by the durable event watermark.
        with self.http.stream("GET", f"/events/chunks/{chunk}") as response:
            response.raise_for_status()
            yield from response.iter_lines()
