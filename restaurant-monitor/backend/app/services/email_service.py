# إرسال رمور OTP للبريد الإلكتروني عند الاسترجاع
import smtplib
from email.message import EmailMessage

from app.core.config import settings


def send_otp_email(
    recipient: str,
    otp: str,
) -> None:

    message = EmailMessage()

    message["Subject"] = "Restaurant Monitoring - Password Reset OTP"
    message["From"] = settings.SMTP_FROM
    message["To"] = recipient

    message.set_content(
        f"""
Your password reset OTP is:

{otp}

This code will expire in 10 minutes.

If you did not request this, please ignore this email.
"""
    )

    with smtplib.SMTP_SSL(
        settings.SMTP_HOST,
        settings.SMTP_PORT,
    ) as smtp:

        smtp.login(
            settings.SMTP_USER,
            settings.SMTP_PASSWORD,
        )

        smtp.send_message(message)