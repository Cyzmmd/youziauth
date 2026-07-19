# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import dataclasses
import urllib.error
import urllib.request
from typing import Callable


CONNECTIVITY_URL = "https://www.msftconnecttest.com/connecttest.txt"
CONNECTIVITY_BODY = "Microsoft Connect Test"


@dataclasses.dataclass(frozen=True)
class NetworkObservation:
    internet_ok: bool
    portal_reachable: bool


def check_external_internet(timeout: int) -> bool:
    request = urllib.request.Request(
        CONNECTIVITY_URL,
        headers={"User-Agent": "youziauth-connectivity-check/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(256).decode("utf-8", errors="replace").strip()
            return response.status == 200 and text == CONNECTIVITY_BODY
    except (OSError, urllib.error.URLError):
        return False


def check_portal_reachable(url: str, timeout: int) -> bool:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "youziauth-portal-probe/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, urllib.error.URLError):
        return False


class NetworkProbe:
    def __init__(
        self,
        internet_check: Callable[[int], bool] = check_external_internet,
        portal_check: Callable[[str, int], bool] = check_portal_reachable,
    ):
        self.internet_check = internet_check
        self.portal_check = portal_check

    def observe(self, config) -> NetworkObservation:
        timeout = max(1, min(int(config.request_timeout_seconds), 8))
        if self.internet_check(timeout):
            return NetworkObservation(True, False)
        return NetworkObservation(
            False,
            self.portal_check(config.portal_url, timeout),
        )
