# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import argparse
import configparser
import dataclasses
import enum
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping, Optional

from auth_runtime import AuthAttempt, AttemptKind


DEFAULT_PORTAL_URL = "http://222.198.127.170"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.ini")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class AuthStatus(enum.Enum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class AuthConfig:
    portal_url: str = DEFAULT_PORTAL_URL
    username: str = ""
    password: str = ""
    login_url: str = ""
    service: str = ""
    check_interval_seconds: int = 60
    request_timeout_seconds: int = 8
    password_encrypt: Optional[bool] = None
    log_file: str = "campus_auth.log"


@dataclasses.dataclass(frozen=True)
class LoginResult:
    ok: bool
    message: str
    raw: str = ""
    data: Optional[Mapping[str, object]] = None


@dataclasses.dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    text: str
    url: str


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_portal_url(url: str) -> str:
    normalized = (url or DEFAULT_PORTAL_URL).strip().rstrip("/")
    if not normalized:
        return DEFAULT_PORTAL_URL
    if "://" not in normalized:
        normalized = "http://" + normalized
    return normalized


def parse_positive_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return parsed


def parse_password_encrypt(value: Optional[str]) -> Optional[bool]:
    normalized = (value or "auto").strip().lower()
    if normalized in ("", "auto"):
        return None
    if normalized in ("1", "yes", "true", "on"):
        return True
    if normalized in ("0", "no", "false", "off"):
        return False
    raise ValueError("password_encrypt must be auto, true, or false")


def resolve_password(section: configparser.SectionProxy) -> str:
    password = section.get("password", "")
    if password:
        return password

    password_env = section.get("password_env", "").strip()
    if password_env:
        env_password = os.environ.get(password_env, "")
        if env_password:
            return env_password
        raise ValueError(f"environment variable {password_env} is required for password")

    raise ValueError("password is required in [auth]")


def load_config_from_parser(parser: configparser.ConfigParser) -> AuthConfig:
    if not parser.has_section("auth"):
        raise ValueError("config must contain an [auth] section")

    section = parser["auth"]
    username = section.get("username", "").strip()
    if not username:
        raise ValueError("username is required in [auth]")
    password = resolve_password(section)

    return AuthConfig(
        portal_url=normalize_portal_url(section.get("portal_url", DEFAULT_PORTAL_URL)),
        username=username,
        password=password,
        login_url=section.get("login_url", "").strip(),
        service=section.get("service", ""),
        check_interval_seconds=parse_positive_int(
            section.get("check_interval_seconds", "60"), "check_interval_seconds"
        ),
        request_timeout_seconds=parse_positive_int(
            section.get("request_timeout_seconds", "8"), "request_timeout_seconds"
        ),
        password_encrypt=parse_password_encrypt(section.get("password_encrypt", "auto")),
        log_file=section.get("log_file", "campus_auth.log"),
    )


def load_config(path: Path) -> AuthConfig:
    parser = configparser.ConfigParser(interpolation=None)
    read_files = parser.read(path, encoding="utf-8")
    if not read_files:
        raise FileNotFoundError(f"config file not found: {path}")
    return load_config_from_parser(parser)


QUERY_STRING_PATTERNS = (
    re.compile(r"(?:index|portal)\.jsp\?([^'\"\s<>]+)", re.IGNORECASE),
    re.compile(r"\bjsp\?([^'\"\s<>]+)", re.IGNORECASE),
    re.compile(r"(wlanuserip=[^'\"\s<>]+)", re.IGNORECASE),
)

LOGIN_QUERY_KEYS = (
    "wlanuserip",
    "wlanacname",
    "wlanacip",
    "nasip",
    "userip",
    "ssid",
)


def looks_like_login_query_string(query: str) -> bool:
    lowered = urllib.parse.unquote_plus(query or "").lower()
    return any(f"{key}=" in lowered for key in LOGIN_QUERY_KEYS)


def extract_query_string(page_text: str) -> Optional[str]:
    if not page_text:
        return None

    decoded = html.unescape(page_text)
    for pattern in QUERY_STRING_PATTERNS:
        match = pattern.search(decoded)
        if match:
            query = match.group(1).strip()
            if query and looks_like_login_query_string(query):
                return query
    return None


def parse_login_result(response_text: str) -> LoginResult:
    raw = response_text or ""
    stripped = raw.strip()
    lowered = stripped.lower()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        result = str(data.get("result", "")).lower()
        message = str(data.get("message") or data.get("msg") or "")
        ok = result == "success" or data.get("success") is True
        if ok and not message:
            message = "success"
        if not ok and not message:
            message = "login failed"
        return LoginResult(ok=ok, message=message, raw=raw, data=data)

    ok = "success" in lowered or "认证成功" in stripped
    message = "success" if ok else stripped[:200]
    return LoginResult(ok=ok, message=message, raw=raw)


def parse_online_user_info(response_text: str) -> AuthStatus:
    try:
        data = json.loads((response_text or "").strip())
    except json.JSONDecodeError:
        return AuthStatus.UNKNOWN

    if not isinstance(data, dict):
        return AuthStatus.UNKNOWN

    result = str(data.get("result", "")).lower()
    if result == "success":
        return AuthStatus.AUTHENTICATED
    if result == "fail":
        return AuthStatus.UNAUTHENTICATED
    return AuthStatus.UNKNOWN


def extract_rsa_public_key(page_info: Mapping[str, object]) -> tuple[str, str]:
    exponent = str(page_info.get("publicKeyExponent") or "").strip()
    modulus = str(page_info.get("publicKeyModulus") or "").strip()
    if not exponent:
        raise ValueError("publicKeyExponent is required for encrypted login")
    if not modulus:
        raise ValueError("publicKeyModulus is required for encrypted login")
    try:
        int(exponent, 16)
    except ValueError as exc:
        raise ValueError("publicKeyExponent must be hexadecimal") from exc
    try:
        int(modulus, 16)
    except ValueError as exc:
        raise ValueError("publicKeyModulus must be hexadecimal") from exc
    return exponent, modulus


def rsa_encrypt_password(password: str, exponent: str, modulus: str) -> str:
    e = int(exponent, 16)
    m = int(modulus, 16)
    password_bytes = password.encode("utf-8")
    password_int = int.from_bytes(password_bytes, byteorder="big")
    if password_int >= m:
        raise ValueError("password is too long for the RSA modulus returned by ePortal")
    encrypted_int = pow(password_int, e, m)
    encrypted_length = (m.bit_length() + 7) // 8
    return encrypted_int.to_bytes(encrypted_length, byteorder="big").hex()


def page_info_requires_password_encryption(page_info: Mapping[str, object]) -> bool:
    return str(page_info.get("passwordEncrypt") or "").strip().lower() == "true"


def build_login_payload(
    config: AuthConfig,
    query_string: str,
    password_value: Optional[str] = None,
    password_encrypt: Optional[bool] = None,
) -> dict[str, str]:
    effective_password_encrypt = (
        config.password_encrypt if password_encrypt is None else password_encrypt
    )
    return {
        "userId": config.username,
        "password": config.password if password_value is None else password_value,
        "service": config.service,
        "queryString": query_string,
        "operatorPwd": "",
        "operatorUserId": "",
        "validcode": "",
        "passwordEncrypt": "true" if effective_password_encrypt else "false",
    }


def configure_logging(log_file: str, verbose: bool) -> logging.Logger:
    logger = logging.getLogger("campus_auth")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(stream_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger


class CampusAuthClient:
    def __init__(self, config: AuthConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger("campus_auth")
        self.opener = urllib.request.build_opener()
        self.no_redirect_opener = urllib.request.build_opener(NoRedirectHandler)

    def portal_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.config.portal_url + path

    def request(
        self,
        url: str,
        data: Optional[Mapping[str, str]] = None,
        allow_redirects: bool = True,
        referer: Optional[str] = None,
    ) -> HttpResponse:
        encoded_data = None
        method = "GET"
        if data is not None:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")
            method = "POST"

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if referer:
            headers["Referer"] = referer

        request = urllib.request.Request(
            url=url, data=encoded_data, headers=headers, method=method
        )
        opener = self.opener if allow_redirects else self.no_redirect_opener

        try:
            response = opener.open(request, timeout=self.config.request_timeout_seconds)
        except urllib.error.HTTPError as exc:
            text = self._read_response_text(exc)
            return HttpResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()),
                text=text,
                url=exc.geturl(),
            )

        with response:
            text = self._read_response_text(response)
            return HttpResponse(
                status_code=response.status,
                headers=dict(response.headers.items()),
                text=text,
                url=response.geturl(),
            )

    @staticmethod
    def _read_response_text(response) -> str:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    def check_status(self) -> AuthStatus:
        for checker in (
            self.check_login_page_status,
            self.check_online_user_info,
            self.check_redirect_status,
        ):
            status = checker()
            if status is not AuthStatus.UNKNOWN:
                return status
        return AuthStatus.UNKNOWN

    def check_online_user_info(self) -> AuthStatus:
        url = self.portal_url("/eportal/InterFace.do?method=getOnlineUserInfo")
        try:
            response = self.request(url)
        except OSError as exc:
            self.logger.debug("getOnlineUserInfo failed: %s", exc)
            return AuthStatus.UNKNOWN
        return parse_online_user_info(response.text)

    def check_redirect_status(self) -> AuthStatus:
        url = self.portal_url("/eportal/redirectortosuccess.jsp")
        try:
            response = self.request(url, allow_redirects=False)
        except OSError as exc:
            self.logger.debug("redirect status check failed: %s", exc)
            return AuthStatus.UNKNOWN

        location = response.headers.get("Location") or response.headers.get("location") or ""
        lowered_location = location.lower()
        if "success.jsp" in lowered_location:
            return AuthStatus.AUTHENTICATED
        if location:
            return AuthStatus.UNAUTHENTICATED
        if "success.jsp" in response.text.lower():
            return AuthStatus.AUTHENTICATED
        return AuthStatus.UNKNOWN

    def check_login_page_status(self) -> AuthStatus:
        try:
            response = self.request(self.config.portal_url)
        except OSError as exc:
            self.logger.debug("login page status check failed: %s", exc)
            return AuthStatus.UNKNOWN

        lowered_url = response.url.lower()
        lowered = response.text.lower()
        if (
            "success" in lowered_url
            or "success.jsp" in lowered
            or "login success" in lowered
            or "登录成功" in response.text
            or "logout" in lowered
            or "userindex" in lowered
        ):
            return AuthStatus.AUTHENTICATED

        redirected_query = urllib.parse.urlparse(response.url).query
        if (
            extract_query_string(response.text)
            or looks_like_login_query_string(redirected_query)
        ):
            return AuthStatus.UNAUTHENTICATED
        return AuthStatus.UNKNOWN

    def get_query_string(self) -> str:
        if self.config.login_url:
            configured_query = urllib.parse.urlparse(self.config.login_url).query
            if looks_like_login_query_string(configured_query):
                return html.unescape(configured_query)
            raise RuntimeError("configured login_url does not contain an ePortal login queryString")

        response = self.request(self.config.portal_url)
        redirected_query = urllib.parse.urlparse(response.url).query
        if redirected_query:
            return html.unescape(redirected_query)

        extracted = extract_query_string(response.text)
        if extracted:
            return extracted

        raise RuntimeError(
            "could not find ePortal queryString; make sure this machine is on the campus network"
        )

    def get_page_info(self, query_string: str) -> Mapping[str, object]:
        response = self.request(
            self.portal_url("/eportal/InterFace.do?method=pageInfo"),
            data={"queryString": query_string},
            referer=self.portal_url("/eportal/index.jsp"),
        )
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ePortal pageInfo did not return JSON: {response.text[:200]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("ePortal pageInfo returned an unexpected JSON value")
        return data

    def login(self) -> LoginResult:
        query_string = self.get_query_string()
        password_value = None
        should_encrypt = self.config.password_encrypt
        page_info: Optional[Mapping[str, object]] = None
        if should_encrypt is None:
            page_info = self.get_page_info(query_string)
            should_encrypt = page_info_requires_password_encryption(page_info)
        if should_encrypt:
            if page_info is None:
                page_info = self.get_page_info(query_string)
            exponent, modulus = extract_rsa_public_key(page_info)
            password_value = rsa_encrypt_password(self.config.password, exponent, modulus)
        payload = build_login_payload(
            self.config,
            query_string,
            password_value=password_value,
            password_encrypt=should_encrypt,
        )
        login_url = self.portal_url("/eportal/InterFace.do?method=login")
        referer = self.portal_url("/eportal/index.jsp")
        response = self.request(login_url, data=payload, referer=referer)
        return parse_login_result(response.text)


def attempt_authentication(client: CampusAuthClient, logger: logging.Logger) -> AuthAttempt:
    try:
        status = client.check_status()
        logger.info("current auth status: %s", status.value)
        if status is AuthStatus.AUTHENTICATED:
            return AuthAttempt(AttemptKind.ALREADY_ONLINE, "already authenticated")
        result = client.login()
    except Exception as exc:  # noqa: BLE001 - CLI tool should log and keep running.
        logger.error("login attempt failed before server response: %s", exc)
        return AuthAttempt(AttemptKind.TRANSIENT_ERROR, str(exc))

    if result.ok:
        logger.info("login succeeded: %s", result.message)
        return AuthAttempt(AttemptKind.LOGIN_SUCCEEDED, result.message)

    logger.error("login failed: %s", result.message)
    return AuthAttempt(AttemptKind.REJECTED, result.message)


def run_once(client: CampusAuthClient, logger: logging.Logger) -> bool:
    attempt = attempt_authentication(client, logger)
    return attempt.kind in (AttemptKind.ALREADY_ONLINE, AttemptKind.LOGIN_SUCCEEDED)


def run_forever(client: CampusAuthClient, logger: logging.Logger) -> None:
    while True:
        run_once(client, logger)
        time.sleep(client.config.check_interval_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automatically keep a Ruijie/ePortal campus network session online."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config file. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check status and attempt login once, then exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug logs.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - CLI should show config errors cleanly.
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    logger = configure_logging(config.log_file, args.verbose)
    client = CampusAuthClient(config, logger)
    if args.once:
        return 0 if run_once(client, logger) else 1

    run_forever(client, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
