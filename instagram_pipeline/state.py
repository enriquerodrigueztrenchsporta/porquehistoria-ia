"""Small publication ledger with compare-and-swap persistence across cloud runs.

Only metadata is persisted. Never put tokens, upload URLs or scraped pages here.
An unsuccessful remote checkpoint prevents the next publication operation.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class StateError(RuntimeError):
    pass


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Ledger:
    def __init__(self, path: Path, repository: str | None = None,
                 token: str | None = None, branch: str = "main"):
        self.path = Path(path)
        self.repository, self.token, self.branch = repository, token, branch
        self.sha = None
        if repository and (not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not token):
            raise StateError("Repositorio o autorización de GitHub ausente.")
        if repository:
            record = self._request("GET")
            self.sha = record["sha"]
            self.data = json.loads(base64.b64decode(record["content"]))
        elif self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"schema": 1, "days": {}, "used_topics": []}
        if self.data.get("schema") != 1 or not isinstance(self.data.get("days"), dict):
            raise StateError("El registro de publicaciones tiene un formato incompatible.")

    def _request(self, method, body=None):
        url = f"https://api.github.com/repos/{self.repository}/contents/data/publication-state.json"
        if method == "GET":
            url += "?ref=" + quote(self.branch, safe="")
        request = Request(url, method=method, data=json.dumps(body).encode() if body else None,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/vnd.github+json",
                                   "User-Agent": "porquehistoria-pipeline",
                                   "Content-Type": "application/json",
                                   "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urlopen(request, timeout=40) as response:
                return json.load(response)
        except HTTPError as exc:
            raise StateError(f"No se pudo guardar/leer el registro remoto: HTTP {exc.code}. No se publicará.") from None
        except OSError:
            raise StateError("GitHub no confirmó el registro remoto. No se publicará.") from None

    def save(self):
        encoded = json.dumps(self.data, ensure_ascii=False, indent=2).encode()
        # Refuse accidental secret persistence even if a future caller changes schemas.
        lowered = encoded.lower()
        if any(key in lowered for key in (b'"access_token"', b'"authorization"', b'"upload_uri"')):
            raise StateError("El registro contiene un campo reservado para credenciales.")
        if self.repository:
            result = self._request("PUT", {"message": "Record daily publication state [skip ci]",
                                          "content": base64.b64encode(encoded).decode(),
                                          "sha": self.sha, "branch": self.branch})
            self.sha = result["content"]["sha"]
        atomic_json(self.path, self.data)
