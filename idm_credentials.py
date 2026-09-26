"""西南大学统一认证（IDM）账号密码的本机加密存储。

设计取舍（重要）：
    本项目原先刻意「不保存密码」，IDM 登录页完全由人工输入。
    启用静默登录后，程序需要在无人工干预时自动填写账号密码，
    因此必须在本机持久化一份凭据。

安全措施：
    * 复用 windows_credentials 的 DPAPI 保护（与校园网密码同一机制），
      密文只能被本机同一用户解密，拷贝到别的机器无法还原；
    * 凭据文件放在用户级 LOCALAPPDATA 目录，不写入 ProgramData 共享位置，
      避免多用户机器上被其他账户读取；
    * 不打印、不记录、不上传密码明文；仅提供 save/load/clear 三个操作。

注意：启用该存储意味着**本机被完全控制时密码可被解密**，
      这与所有「记住密码」类功能的固有风险相同。README/SECURITY 已说明。
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from windows_credentials import CredentialError, CredentialStore, DpapiProtector

# 复用同一个 CredentialStore：它把一段字符串用 DPAPI 加密后写入 credential.dat。
# 这里把用户名与密码一起放在同一段 JSON 里，避免两个文件不同步。
RECORD_VERSION = 1


@dataclasses.dataclass(frozen=True)
class IdmCredentials:
    username: str
    password: str

    def validate(self) -> "IdmCredentials":
        if not self.username.strip():
            raise ValueError("统一认证用户名不能为空")
        if not self.password:
            raise ValueError("统一认证密码不能为空")
        # 凭据被放在单行 JSON 中，换行会破坏结构（也顺带防止注入）
        if any(c in self.username for c in "\r\n") or any(c in self.password for c in "\r\n"):
            raise ValueError("用户名或密码不能包含换行符")
        # IDToken1 走页面 JS 加密，但保守起见限制长度，避免异常输入
        if len(self.username) > 64 or len(self.password) > 128:
            raise ValueError("用户名或密码长度超出预期范围")
        return self


def default_root() -> Path:
    """凭据存放根目录：用户级 LOCALAPPDATA/youziauth/idm。"""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "youziauth" / "idm"


class IdmCredentialStore:
    """IDM 凭据的保存/读取/清除。"""

    def __init__(self, root: Path | None = None, protector=None):
        self.root = Path(root) if root is not None else default_root()
        self._store = CredentialStore(self.root, protector or DpapiProtector(machine_scope=False))

    @property
    def path(self) -> Path:
        return self._store.credential_path

    def save(self, credentials: IdmCredentials) -> None:
        creds = credentials.validate()
        payload = json.dumps(
            {"v": RECORD_VERSION, "username": creds.username, "password": creds.password},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._store.save_password(payload)

    def load(self) -> IdmCredentials | None:
        """读取凭据；不存在返回 None，解密失败抛 CredentialError。"""
        if not self.path.exists():
            return None
        raw = self._store.load_password()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CredentialError(f"统一认证凭据格式无效: {exc}") from exc
        if not isinstance(data, dict) or data.get("v") != RECORD_VERSION:
            raise CredentialError("统一认证凭据版本不匹配，请重新保存")
        username = data.get("username")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise CredentialError("统一认证凭据内容不完整，请重新保存")
        return IdmCredentials(username=username, password=password)

    def exists(self) -> bool:
        return self.path.exists()

    def clear(self) -> None:
        self._store.clear_password()
