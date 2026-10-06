import abc
import json
import logging
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("aura.email")


class EmailSender(abc.ABC):
    """Lớp trừu tượng cho dịch vụ gửi email trong hệ thống."""

    @abc.abstractmethod
    def send_reset_email(self, to_email: str, reset_link: str) -> bool:
        """Gửi link đặt lại mật khẩu đến email được chỉ định."""
        pass


class ConsoleEmailSender(EmailSender):
    """Bản dev: In link đặt lại mật khẩu trực tiếp ra Console / Log."""

    def send_reset_email(self, to_email: str, reset_link: str) -> bool:
        msg = f"[EMAIL DEV] Link đặt lại mật khẩu cho {to_email}: {reset_link}"
        logger.info("======================================================================")
        logger.info(msg)
        logger.info("======================================================================")
        return True


class SMTPEmailSender(EmailSender):
    """Bản sản xuất: Gửi mail qua máy chủ SMTP (Gmail, Outlook, Amazon SES, v.v.)."""

    def send_reset_email(self, to_email: str, reset_link: str) -> bool:
        if not settings.SMTP_HOST or not settings.SMTP_USER:
            logger.error("Cấu hình SMTP thiếu SMTP_HOST hoặc SMTP_USER")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{settings.APP_NAME}] Đặt lại mật khẩu tài khoản"
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to_email

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E5E2DC;">
            <h2 style="color: #111111;">AURA Studio - Yêu cầu đặt lại mật khẩu</h2>
            <p>Chào bạn,</p>
            <p>Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản liên kết với email <strong>{to_email}</strong>.</p>
            <p>Vui lòng bấm vào nút dưới đây để đặt mật khẩu mới (link có hiệu lực trong 30 phút):</p>
            <p style="margin: 25px 0;">
                <a href="{reset_link}" style="background-color: #111111; color: #ffffff; padding: 12px 24px; text-decoration: none; display: inline-block; font-weight: bold; border-radius: 2px;">Đặt lại mật khẩu</a>
            </p>
            <p style="color: #777777; font-size: 13px;">Hoặc dán đường dẫn này vào trình duyệt: <br><a href="{reset_link}">{reset_link}</a></p>
            <hr style="border: none; border-top: 1px solid #E5E2DC; margin: 20px 0;">
            <p style="color: #999999; font-size: 12px;">Nếu bạn không yêu cầu đổi mật khẩu, vui lòng bỏ qua email này.</p>
        </div>
        """
        msg.attach(MIMEText(html_body, "html"))

        try:
            if settings.SMTP_TLS:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
                server.starttls()
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)

            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)

            server.sendmail(settings.EMAIL_FROM, [to_email], msg.as_string())
            server.quit()
            return True
        except Exception as e:
            logger.error(f"Lỗi gửi email qua SMTP: {e}")
            return False


class ResendEmailSender(EmailSender):
    """Bản sản xuất: Gửi mail qua dịch vụ Resend REST API (https://resend.com)."""

    def send_reset_email(self, to_email: str, reset_link: str) -> bool:
        if not settings.RESEND_API_KEY:
            logger.error("Thiếu RESEND_API_KEY trong cấu hình")
            return False

        payload = {
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": f"[{settings.APP_NAME}] Đặt lại mật khẩu tài khoản",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E5E2DC;">
                <h2 style="color: #111111;">AURA Studio - Yêu cầu đặt lại mật khẩu</h2>
                <p>Chào bạn,</p>
                <p>Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản liên kết với email <strong>{to_email}</strong>.</p>
                <p>Vui lòng bấm vào nút dưới đây để đặt mật khẩu mới (link có hiệu lực trong 30 phút):</p>
                <p style="margin: 25px 0;">
                    <a href="{reset_link}" style="background-color: #111111; color: #ffffff; padding: 12px 24px; text-decoration: none; display: inline-block; font-weight: bold; border-radius: 2px;">Đặt lại mật khẩu</a>
                </p>
                <p style="color: #777777; font-size: 13px;">Hoặc dán đường dẫn này vào trình duyệt: <br><a href="{reset_link}">{reset_link}</a></p>
                <hr style="border: none; border-top: 1px solid #E5E2DC; margin: 20px 0;">
                <p style="color: #999999; font-size: 12px;">Nếu bạn không yêu cầu đổi mật khẩu, vui lòng bỏ qua email này.</p>
            </div>
            """
        }

        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.error(f"Lỗi gửi email qua Resend API: {e}")
            return False


def get_email_sender() -> EmailSender:
    """Factory khởi tạo EmailSender theo cấu hình EMAIL_BACKEND."""
    backend = (settings.EMAIL_BACKEND or "console").strip().lower()
    if backend == "smtp":
        return SMTPEmailSender()
    elif backend == "resend":
        return ResendEmailSender()
    return ConsoleEmailSender()
