"""Отправка писем через SMTP Яндекс Почты.

Нужно: в Яндекс Почте включить «Почтовые программы → С сервера imap.yandex.ru / SMTP» и создать
пароль приложения (https://id.yandex.ru/security/app-passwords). Логин — полный адрес, адрес «От»
обязан совпадать с логином, иначе Яндекс отклонит письмо.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Callable, Optional

from app.config import Settings

log = logging.getLogger(__name__)


class MailerError(RuntimeError):
    pass


def build_message(settings: Settings, to: str, subject: str, text: str, html: str,
                  images: dict[str, bytes], pdf: Optional[tuple[str, bytes]] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_user))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=settings.smtp_user.split("@")[-1] or "localhost")
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    html_part = msg.get_payload()[1]
    for cid, png in images.items():
        html_part.add_related(png, maintype="image", subtype="png", cid=f"<{cid}>", filename=f"{cid}.png",
                              disposition="inline")
    if pdf:
        name, data = pdf
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return msg


class Mailer:
    def __init__(self, settings: Settings, smtp_factory: Optional[Callable[[], smtplib.SMTP]] = None):
        self.s = settings
        self._factory = smtp_factory or self._default_factory
        self._conn: Optional[smtplib.SMTP] = None

    def _default_factory(self) -> smtplib.SMTP:
        s = self.s
        if s.smtp_security == "starttls":
            conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
            conn.starttls(context=ssl.create_default_context())
            return conn
        return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context())

    def _connect(self) -> smtplib.SMTP:
        if not self.s.smtp_user or not self.s.smtp_password:
            raise MailerError("Не заданы SMTP_USER / SMTP_PASSWORD (пароль приложения Яндекса).")
        conn = self._factory()
        try:
            conn.login(self.s.smtp_user, self.s.smtp_password)
        except smtplib.SMTPAuthenticationError as exc:
            reply = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else exc.smtp_error
            raise MailerError(f"Яндекс отклонил логин/пароль (ответ сервера: {exc.smtp_code} {reply}). "
                              "Нужен пароль приложения (не пароль от аккаунта), "
                              "а в настройках почты должен быть включён доступ по SMTP.") from exc
        return conn

    def check(self) -> None:
        conn = self._connect()
        conn.quit()

    def send(self, msg: EmailMessage, attempts: int = 3) -> None:
        last: Optional[Exception] = None
        for i in range(1, attempts + 1):
            try:
                if self._conn is None:
                    self._conn = self._connect()
                self._conn.send_message(msg)
                return
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPResponseException,
                    ConnectionError, TimeoutError, ssl.SSLError) as exc:
                if isinstance(exc, smtplib.SMTPResponseException) and 500 <= exc.smtp_code < 600:
                    raise MailerError(f"SMTP отклонил письмо: {exc.smtp_code} {exc.smtp_error!r}") from exc
                last = exc
                log.warning("SMTP: попытка %s/%s не удалась: %s", i, attempts, exc)
                self.close()
                time.sleep(min(2 ** i, 10) if self.s.smtp_pause_sec else 0)
        raise MailerError(f"Не удалось отправить письмо после {attempts} попыток: {last}")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.quit()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
