"""
Forex News Email Alert
======================
ดึงข่าวจาก Forex Factory แล้วส่งเข้า Gmail

ไม่ใช้ Claude API
ไม่วิเคราะห์ข่าวด้วย AI
ส่งเฉพาะข่าวสำคัญ USD / JPY / GBP
"""

import os
import smtplib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= CONFIG =================

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_RECIPIENT = os.environ["GMAIL_RECIPIENT"]

TARGET_CURRENCIES = ["USD", "JPY", "GBP"]
TARGET_IMPACTS = ["High", "Medium"]

FOREX_FACTORY_XML = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"


# ================= FETCH NEWS =================

def fetch_forex_factory_news():
    """
    ดึงข่าวจาก Forex Factory XML
    กรองเฉพาะ USD / JPY / GBP และ High / Medium impact
    """
    try:
        response = requests.get(
            FOREX_FACTORY_XML,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )
        response.raise_for_status()

        root = ET.fromstring(response.content)
        news = []

        for event in root.findall("event"):
            title = event.findtext("title", default="-")
            currency = event.findtext("country", default="-")
            date = event.findtext("date", default="-")
            time = event.findtext("time", default="-")
            impact = event.findtext("impact", default="-")
            forecast = event.findtext("forecast", default="-")
            previous = event.findtext("previous", default="-")

            if currency not in TARGET_CURRENCIES:
                continue

            if impact not in TARGET_IMPACTS:
                continue

            news.append({
                "date": date,
                "time": time,
                "currency": currency,
                "impact": impact,
                "title": title,
                "forecast": forecast,
                "previous": previous,
            })

        print(f"Fetched {len(news)} news items")
        return news

    except Exception as e:
        print(f"Error fetching Forex Factory news: {e}")
        return []


# ================= BUILD EMAIL =================

def build_email(news):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    high_count = sum(1 for n in news if n["impact"] == "High")
    medium_count = sum(1 for n in news if n["impact"] == "Medium")

    subject = f"Forex News Alert | {now} | High {high_count} / Medium {medium_count}"

    if not news:
        rows = """
        <tr>
            <td colspan="7" style="padding:16px;text-align:center;color:#888;">
                ไม่มีข่าว High / Medium ของ USD, JPY, GBP ในรอบนี้
            </td>
        </tr>
        """
    else:
        rows = ""
        for n in news:
            impact_color = "#e74c3c" if n["impact"] == "High" else "#f39c12"
            impact_text = "🔴 HIGH" if n["impact"] == "High" else "🟠 MEDIUM"

            rows += f"""
            <tr>
                <td>{n["date"]}</td>
                <td>{n["time"]}</td>
                <td><b>{n["currency"]}</b></td>
                <td>
                    <span style="background:{impact_color};color:white;padding:3px 8px;border-radius:10px;font-size:11px;">
                        {impact_text}
                    </span>
                </td>
                <td>{n["title"]}</td>
                <td>{n["forecast"] or "-"}</td>
                <td>{n["previous"] or "-"}</td>
            </tr>
            """

    html = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                padding: 20px;
                color: #222;
            }}
            .container {{
                max-width: 900px;
                margin: auto;
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            }}
            .header {{
                background: #111827;
                color: white;
                padding: 22px;
            }}
            .header h1 {{
                margin: 0;
                font-size: 22px;
            }}
            .header p {{
                margin: 6px 0 0;
                font-size: 13px;
                opacity: 0.8;
            }}
            .content {{
                padding: 22px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
            }}
            th {{
                background: #f1f5f9;
                text-align: left;
                padding: 10px;
                color: #555;
            }}
            td {{
                border-bottom: 1px solid #eee;
                padding: 10px;
                vertical-align: top;
            }}
            .note {{
                margin-top: 18px;
                background: #fff8e1;
                border-left: 4px solid #ffc107;
                padding: 12px;
                font-size: 13px;
                color: #555;
            }}
            .footer {{
                text-align: center;
                color: #999;
                font-size: 12px;
                padding: 16px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Forex News Alert</h1>
                <p>USD / JPY / GBP | Generated at {now}</p>
            </div>

            <div class="content">
                <h3>ข่าว High / Medium Impact จาก Forex Factory</h3>

                <table>
                    <thead>
                        <tr>
                            <th>วันที่</th>
                            <th>เวลา</th>
                            <th>สกุลเงิน</th>
                            <th>Impact</th>
                            <th>ข่าว</th>
                            <th>Forecast</th>
                            <th>Previous</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>

                <div class="note">
                    วิธีใช้: ถ้ามีข่าวสำคัญ ให้คัดลอกข่าวนี้มาถาม ChatGPT ต่อ เช่น
                    “ข่าวนี้มีผลกับ USDJPY ยังไง ควรรอหรือเทรดได้ไหม”
                </div>
            </div>

            <div class="footer">
                Forex News Alert System · Forex Factory Calendar
            </div>
        </div>
    </body>
    </html>
    """

    return subject, html


# ================= SEND EMAIL =================

def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = GMAIL_RECIPIENT
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_SENDER, GMAIL_RECIPIENT, msg.as_string())

    print("Email sent successfully")


# ================= MAIN =================

def main():
    print("Starting Forex News Alert")

    news = fetch_forex_factory_news()
    subject, html = build_email(news)
    send_email(subject, html)

    print("Done")


if __name__ == "__main__":
    main()