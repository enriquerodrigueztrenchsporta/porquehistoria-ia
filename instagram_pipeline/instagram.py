"""Official Instagram Reel publishing with durable, conservative retry semantics.

No request publishes until ``publish_prepared`` is called. Keep each journal:
deleting it discards the protection against duplicate publication.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urlencode, urlsplit


class InstagramError(RuntimeError):
    """A sanitized error that never includes credentials or response bodies."""


class AmbiguousPublishError(InstagramError):
    """A publish was sent; reconcile with Instagram before any manual retry."""


@dataclass(frozen=True)
class InstagramConfig:
    user_id: str
    access_token: str = field(repr=False)
    api_version: str = "v25.0"
    login_mode: str = "facebook"
    public_video_url: str | None = None
    timeout_seconds: float = 120
    poll_seconds: float = 60
    poll_attempts: int = 6
    expected_username: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[0-9]+", self.user_id):
            raise ValueError("Instagram user_id debe ser un identificador numérico.")
        if not self.access_token or any(c in self.access_token for c in "\r\n"):
            raise ValueError("Falta un token válido de Instagram/Meta.")
        if not re.fullmatch(r"v[0-9]+\.0", self.api_version):
            raise ValueError("La versión debe tener formato v25.0.")
        if self.login_mode not in {"facebook", "instagram"}:
            raise ValueError("login_mode debe ser facebook o instagram.")
        if self.poll_attempts < 1 or self.poll_seconds < 0 or self.timeout_seconds <= 0:
            raise ValueError("Los límites de espera no son válidos.")
        if self.expected_username is not None and not re.fullmatch(r"[A-Za-z0-9_.]+", self.expected_username):
            raise ValueError("expected_username debe ser el nombre de Instagram sin @.")

    @property
    def origin(self):
        host = "graph.facebook.com" if self.login_mode == "facebook" else "graph.instagram.com"
        return f"https://{host}/{self.api_version}"


def _http_request(method, url, fields=None, headers=None, body_path=None, timeout=120):
    """HTTPS without redirects, automatic POST retries or token-bearing logs."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "graph.facebook.com", "graph.instagram.com", "rupload.facebook.com"
    } or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise InstagramError("Destino API no permitido.")
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    request_headers = dict(headers or {})
    body = None
    if fields:
        encoded = urlencode(fields)
        if method == "GET":
            path += ("&" if parsed.query else "?") + encoded
        else:
            body = encoded.encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout)
    try:
        if body_path is not None:
            request_headers["Content-Length"] = str(Path(body_path).stat().st_size)
            request_headers["Content-Type"] = "application/octet-stream"
            with Path(body_path).open("rb") as stream:
                connection.request(method, path, body=stream, headers=request_headers)
        else:
            connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read(1024 * 1024)
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise InstagramError(f"Meta devolvió una respuesta ilegible (HTTP {response.status}).") from None
        if not isinstance(data, dict):
            raise InstagramError("Respuesta API inesperada.")
        if response.status >= 300 or "error" in data:
            # Meta error messages can echo request values, so report numeric codes only.
            error = data.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            subcode = error.get("error_subcode") if isinstance(error, dict) else None
            safe_code = code if isinstance(code, int) else "?"
            safe_subcode = subcode if isinstance(subcode, int) else "?"
            raise InstagramError(f"Meta rechazó la petición: HTTP {response.status}, código {safe_code}, subcódigo {safe_subcode}.")
        return data
    except (OSError, http.client.HTTPException):
        raise InstagramError("No se recibió confirmación de Meta; comprueba conexión y diario.") from None
    finally:
        connection.close()


def _save(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def _journal_lock(path: Path):
    """An OS lock is released automatically if a process crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise InstagramError("Ya hay otro proceso usando este diario.") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise InstagramError("Ya hay otro proceso usando este diario.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(path: Path):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise InstagramError("No se puede leer el diario; no se publicará para evitar duplicados.") from None
    if not isinstance(record, dict):
        raise InstagramError("Diario inválido.")
    return record


class InstagramPublisher:
    def __init__(self, config: InstagramConfig, *, transport: Callable | None = None,
                 sleeper: Callable = time.sleep, checkpoint: Callable[[dict], None] | None = None):
        self.config = config
        self.transport = transport or _http_request
        self.sleeper = sleeper
        self.checkpoint = checkpoint

    def _save(self, path, record):
        _save(path, record)
        if self.checkpoint is not None:
            # Remote runners must persist this snapshot before the next mutation.
            # Failure deliberately propagates so the request is never transmitted.
            self.checkpoint(dict(record))

    def _graph(self, method, path, fields=None):
        return self.transport(method, f"{self.config.origin}/{path}", fields=fields,
                              headers={"Authorization": f"Bearer {self.config.access_token}"},
                              timeout=self.config.timeout_seconds)

    def verify_account(self):
        """Confirm the numeric id and, when configured, the intended username."""
        account = self._graph("GET", self.config.user_id, {"fields": "id,username"})
        if str(account.get("id")) != self.config.user_id:
            raise InstagramError("La API no confirmó la cuenta configurada.")
        actual_username = str(account.get("username", ""))
        if (self.config.expected_username is not None
                and actual_username.casefold() != self.config.expected_username.casefold()):
            raise InstagramError("El token/ID no corresponde al nombre de Instagram esperado; publicación detenida.")
        return {"id": account["id"], "username": actual_username}

    def check_connection(self):
        """Read-only check; no token or secret in the returned dictionary."""
        account = self.verify_account()
        limits = self._graph("GET", f"{self.config.user_id}/content_publishing_limit",
                             {"fields": "config,quota_usage"})
        return {"id": account["id"], "username": account.get("username"), "limits": limits.get("data", [])}

    def _check_identity(self, record, publish_key):
        if (record.get("publish_key") != publish_key or record.get("user_id") != self.config.user_id
                or record.get("login_mode") != self.config.login_mode):
            raise InstagramError("El diario pertenece a otra fecha, cuenta o modo de conexión.")

    def _status(self, container_id):
        if not re.fullmatch(r"[0-9]+", str(container_id)):
            raise InstagramError("Identificador de contenedor inválido.")
        result = self._graph("GET", str(container_id), {"fields": "status_code"})
        status = result.get("status_code")
        if status not in {"IN_PROGRESS", "FINISHED", "PUBLISHED", "ERROR", "EXPIRED"}:
            raise InstagramError("Estado de contenedor desconocido; publicación detenida.")
        return status

    def _reconcile(self, record, journal_path):
        status = self._status(record["container_id"])
        if status == "PUBLISHED":
            record.update(state="published", reconciled=True)
            self._save(journal_path, record)
            return record
        raise AmbiguousPublishError(
            "Ya se envió una petición de publicación sin confirmación. "
            "Meta aún no confirma PUBLISHED; no se reenviará automáticamente."
        )

    def prepare_reel(self, video_path: Path, caption: str, journal_path: Path, publish_key: str):
        """Upload now, leaving a ready container for a later scheduled publish."""
        video_path, journal_path = Path(video_path), Path(journal_path)
        if not publish_key or len(caption) > 2200:
            raise ValueError("Falta publish_key o el texto supera los 2200 caracteres.")
        if video_path.suffix.lower() != ".mp4" or not video_path.is_file():
            raise ValueError("Se requiere un archivo MP4 existente.")
        if not 0 < video_path.stat().st_size <= 300_000_000:
            raise ValueError("El vídeo debe ocupar entre 1 byte y 300 MB (límite conservador local).")
        with video_path.open("rb") as stream:
            fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
        caption_hash = hashlib.sha256(caption.encode("utf-8")).hexdigest()
        with _journal_lock(journal_path):
            record = _read(journal_path) if journal_path.exists() else None
            if record:
                self._check_identity(record, publish_key)
                if record.get("video_sha256") != fingerprint or record.get("caption_sha256") != caption_hash:
                    raise InstagramError("El contenido ha cambiado respecto al diario; no se reemplazará una publicación pendiente.")
                if record["state"] == "published":
                    return record
                if record["state"] == "publish_requested":
                    return self._reconcile(record, journal_path)
                if not record.get("container_id"):
                    raise InstagramError("Creación de contenedor sin confirmar; revisar el diario antes de reintentar.")
                if record["state"] == "container_created":
                    raise InstagramError("Subida interrumpida antes de enviar el archivo; revisar el diario.")
            else:
                if self.config.expected_username is not None:
                    self.verify_account()
                fields = {"media_type": "REELS", "caption": caption, "share_to_feed": "true"}
                if self.config.login_mode == "facebook":
                    fields["upload_type"] = "resumable"
                else:
                    public_url = urlsplit(self.config.public_video_url or "")
                    if public_url.scheme != "https" or not public_url.hostname or public_url.username or public_url.password:
                        raise InstagramError("Instagram Login necesita public_video_url HTTPS accesible públicamente.")
                    fields["video_url"] = self.config.public_video_url
                record = {"publish_key": publish_key, "user_id": self.config.user_id,
                          "login_mode": self.config.login_mode, "video_sha256": fingerprint,
                          "caption_sha256": caption_hash, "state": "create_requested", "created_at": time.time()}
                self._save(journal_path, record)
                created = self._graph("POST", f"{self.config.user_id}/media", fields)
                container_id = str(created.get("id", ""))
                if not re.fullmatch(r"[0-9]+", container_id):
                    raise InstagramError("Meta no devolvió un identificador de contenedor válido.")
                record.update(container_id=container_id, state="container_created")
                self._save(journal_path, record)
                if self.config.login_mode == "facebook":
                    upload_uri = str(created.get("uri", ""))
                    parsed = urlsplit(upload_uri)
                    if (parsed.scheme != "https" or parsed.hostname != "rupload.facebook.com"
                            or parsed.username or parsed.password or parsed.port not in {None, 443}
                            or not parsed.path.startswith("/ig-api-upload/")):
                        raise InstagramError("Meta devolvió un destino de subida no permitido.")
                    record["state"] = "upload_requested"
                    self._save(journal_path, record)
                    self.transport("POST", upload_uri, headers={
                        "Authorization": f"OAuth {self.config.access_token}",
                        "offset": "0", "file_size": str(video_path.stat().st_size)
                    }, body_path=video_path, timeout=self.config.timeout_seconds)
                record["state"] = "processing"
                self._save(journal_path, record)
            for attempt in range(self.config.poll_attempts):
                status = self._status(record["container_id"])
                record["container_status"] = status
                if status == "FINISHED":
                    record["state"] = "ready"
                    self._save(journal_path, record)
                    return record
                if status == "PUBLISHED":
                    record.update(state="published", reconciled=True)
                    self._save(journal_path, record)
                    return record
                self._save(journal_path, record)
                if status in {"ERROR", "EXPIRED"}:
                    raise InstagramError(f"Meta terminó el contenedor con estado {status}.")
                if attempt + 1 < self.config.poll_attempts:
                    self.sleeper(self.config.poll_seconds)
            raise InstagramError("El vídeo sigue procesándose; se conserva su contenedor para la siguiente comprobación.")

    def publish_prepared(self, journal_path: Path, publish_key: str):
        """Publish once. A lost acknowledgement is reconciled, never blindly retried."""
        journal_path = Path(journal_path)
        with _journal_lock(journal_path):
            record = _read(journal_path)
            self._check_identity(record, publish_key)
            if record["state"] == "published":
                return record
            if record["state"] == "publish_requested":
                return self._reconcile(record, journal_path)
            if record["state"] != "ready":
                raise InstagramError("El vídeo todavía no está preparado para publicar.")
            status = self._status(record["container_id"])
            if status == "PUBLISHED":
                record.update(state="published", reconciled=True)
                self._save(journal_path, record)
                return record
            if status != "FINISHED":
                raise InstagramError(f"El contenedor no se puede publicar: {status}.")
            # Persist BEFORE transmitting: even a process crash cannot resend blindly.
            record.update(state="publish_requested", publish_requested_at=time.time())
            self._save(journal_path, record)
            result = self._graph("POST", f"{self.config.user_id}/media_publish",
                                 {"creation_id": record["container_id"]})
            media_id = str(result.get("id", ""))
            if not re.fullmatch(r"[0-9]+", media_id):
                raise AmbiguousPublishError("Meta recibió la publicación pero no devolvió una confirmación válida.")
            record.update(state="published", media_id=media_id, published_at=time.time())
            self._save(journal_path, record)
            return record


def publish_reel(video_path: Path, caption: str, config: InstagramConfig,
                 journal_path: Path, publish_key: str):
    """Convenience wrapper; scheduled callers should prepare well before 20:00."""
    publisher = InstagramPublisher(config)
    publisher.prepare_reel(video_path, caption, journal_path, publish_key)
    return publisher.publish_prepared(journal_path, publish_key)


def refresh_instagram_token(access_token: str, *, transport: Callable | None = None):
    """Instagram Login only. Caller must securely persist the returned token.

    Meta requires a still-valid long-lived token at least 24 hours old. This
    operation does not refresh Facebook Login/Page tokens.
    """
    result = (transport or _http_request)("GET", "https://graph.instagram.com/refresh_access_token",
        fields={"grant_type": "ig_refresh_token", "access_token": access_token}, timeout=60)
    if not result.get("access_token") or not isinstance(result.get("expires_in"), int):
        raise InstagramError("Meta no devolvió la renovación esperada del token.")
    return {"access_token": result["access_token"], "expires_at": time.time() + result["expires_in"]}
