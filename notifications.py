"""
Email notification system for new reviews.
Uses SMTP for sending emails. Configure via environment variables.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List
from sqlalchemy.orm import Session
from models import User, Review

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@reviewradar.nl")


def send_email(to_email: str, subject: str, html_body: str):
    if not SMTP_USER or not SMTP_PASS:
        print(f"[NOTIFICATION] Email skipped (no SMTP config): {subject} -> {to_email}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())


def notify_new_review(user: User, review: Review):
    if not user.email_notifications:
        return

    stars = "★" * int(review.rating or 0) + "☆" * (5 - int(review.rating or 0))
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">Review<span style="color: #facc15;">Radar</span></h1>
        </div>
        <div style="background: white; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <h2 style="margin-top: 0;">Nieuwe review ontvangen!</h2>
            <div style="background: #f9fafb; border-radius: 8px; padding: 16px; margin: 16px 0;">
                <p style="margin: 0 0 8px 0;"><strong>{review.author_name}</strong> op <strong>{review.platform.title()}</strong></p>
                <p style="margin: 0 0 8px 0; color: #eab308; font-size: 20px;">{stars}</p>
                <p style="margin: 0; color: #4b5563;">{review.text or '(Geen tekst)'}</p>
            </div>
            <a href="https://reviewradar.nl/dashboard.html" style="display: inline-block; background: #2563eb; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold;">Bekijk & Reageer</a>
        </div>
    </div>
    """
    subject = f"Nieuwe {review.platform.title()} review ({int(review.rating or 0)}★) — {review.author_name}"
    send_email(user.email, subject, html)


def send_daily_digest(user: User, new_reviews: List[Review]):
    if not user.email_notifications or not new_reviews:
        return

    review_rows = ""
    for r in new_reviews:
        stars = "★" * int(r.rating or 0) + "☆" * (5 - int(r.rating or 0))
        review_rows += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{r.platform.title()}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #eab308;">{stars}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{r.author_name}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{(r.text or '')[:80]}...</td>
        </tr>
        """

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">Review<span style="color: #facc15;">Radar</span></h1>
        </div>
        <div style="background: white; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <h2 style="margin-top: 0;">Dagelijks overzicht — {len(new_reviews)} nieuwe review(s)</h2>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Platform</th>
                        <th style="padding: 8px; text-align: left;">Rating</th>
                        <th style="padding: 8px; text-align: left;">Auteur</th>
                        <th style="padding: 8px; text-align: left;">Review</th>
                    </tr>
                </thead>
                <tbody>{review_rows}</tbody>
            </table>
            <a href="https://reviewradar.nl/dashboard.html" style="display: inline-block; background: #2563eb; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; margin-top: 16px;">Bekijk alle reviews</a>
        </div>
    </div>
    """
    subject = f"ReviewRadar — {len(new_reviews)} nieuwe review(s) vandaag"
    send_email(user.email, subject, html)
