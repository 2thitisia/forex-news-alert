"""
Forex News Email Alert — Free Version / Mobile Card Layout
==========================================================

ระบบนี้ทำงานแบบไม่เสียเงิน API:
1) Morning Brief เวลา 07:00 ไทย
   - ส่งข่าว High / Medium ของวันนี้ทั้งหมด
   - เฉพาะ USD / JPY / GBP
   - เวลาแสดงเป็นเวลาไทย
   - มี Actual / Forecast / Previous
   - มี Rule-based view เบื้องต้น

2) Pre-news Alert
   - GitHub Actions เช็กทุก 10 นาที
   - ส่งเมลเฉพาะเมื่อมีข่าวใกล้ออกประมาณ 8–15 นาที
   - ลดโอกาสส่งซ้ำ

รูปแบบอีเมล:
- ใช้ Card Layout อ่านง่ายบนมือถือ
"""

import os
import smtplib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ================= CONFIG =================

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_RECIPIENT = os.environ["GMAIL_RECIPIENT"]

TARGET_CURRENCIES = ["USD", "JPY", "GBP"]
TARGET_IMPACTS = ["High", "Medium"]

FOREX_FACTORY_XML = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

SOURCE_TZ = timezone.utc
ICT_TZ = timezone(timedelta(hours=7))


# ================= TIME HELPERS =================

def now_ict():
    return datetime.now(ICT_TZ)


def parse_event_datetime(date_text, time_text):
    if not date_text or not time_text:
        return None

    time_clean = time_text.strip().lower()

    if time_clean in ["all day", "tentative", ""]:
        return None

    try:
        source_dt = datetime.strptime(
            f"{date_text.strip()} {time_clean}",
            "%m-%d-%Y %I:%M%p"
        )
        source_dt = source_dt.replace(tzinfo=SOURCE_TZ)
        return source_dt.astimezone(ICT_TZ)
    except Exception as e:
        print(f"Could not parse datetime: date={date_text}, time={time_text}, error={e}")
        return None


def is_same_ict_day(dt1, dt2):
    return dt1.date() == dt2.date()


def format_ict_date(dt):
    if not dt:
        return "-"
    return dt.strftime("%d/%m/%Y")


def format_ict_time(dt):
    if not dt:
        return "-"
    return dt.strftime("%H:%M")


# ================= RULE-BASED INTERPRETATION =================

def classify_news(title):
    t = title.lower()

    central_bank_keywords = [
        "fomc",
        "federal funds rate",
        "fed interest rate",
        "fed chair",
        "powell",
        "fomc meeting minutes",
        "fomc minutes",
        "interest rate decision",
        "rate statement",
        "monetary policy statement",
        "monetary policy report",
        "press conference",
        "boe gov",
        "boe",
        "bank rate",
        "bailey",
        "boj",
        "ueda",
        "policy rate",
        "official bank rate",
        "mpc",
        "minutes",
        "speaks",
        "speech",
        "testifies",
        "hearing",
    ]

    inflation_keywords = [
        "cpi",
        "core cpi",
        "consumer price",
        "pce",
        "core pce",
        "ppi",
        "producer price",
        "inflation",
        "average earnings",
        "average hourly earnings",
        "wage",
        "wages",
        "earnings index",
    ]

    jobs_good_when_higher_keywords = [
        "non-farm",
        "nonfarm",
        "nfp",
        "payrolls",
        "employment change",
        "adp",
        "claimant count change",
        "employment",
    ]

    jobs_bad_when_higher_keywords = [
        "unemployment rate",
        "unemployment claims",
        "initial jobless claims",
        "jobless claims",
        "continuing claims",
        "claimant count rate",
    ]

    growth_keywords = [
        "gdp",
        "retail sales",
        "pmi",
        "ism",
        "manufacturing",
        "services",
        "consumer confidence",
        "consumer sentiment",
        "uom",
        "durable goods",
        "industrial production",
        "business confidence",
        "construction",
        "housing starts",
        "building permits",
        "pending home sales",
        "new home sales",
        "existing home sales",
        "philly fed",
        "empire state",
        "trade balance",
        "current account",
    ]

    spending_keywords = [
        "personal spending",
        "personal income",
        "consumer credit",
    ]

    if any(k in t for k in central_bank_keywords):
        return "central_bank"

    if any(k in t for k in inflation_keywords):
        return "inflation"

    if any(k in t for k in jobs_bad_when_higher_keywords):
        return "jobs_bad_when_higher"

    if any(k in t for k in jobs_good_when_higher_keywords):
        return "jobs_good_when_higher"

    if any(k in t for k in growth_keywords):
        return "growth"

    if any(k in t for k in spending_keywords):
        return "spending"

    return "general"


def currency_effect_text(currency, direction):
    if currency == "USD":
        if direction == "strong":
            return "USD แข็ง → USDJPY มีโอกาสขึ้น / GBPUSD มีโอกาสลง / XAUUSD มักถูกกดลง"
        return "USD อ่อน → USDJPY มีโอกาสลง / GBPUSD มีโอกาสขึ้น / XAUUSD มักมีแรงหนุน"

    if currency == "JPY":
        if direction == "strong":
            return "JPY แข็ง → USDJPY มีโอกาสลง / GBPJPY มีโอกาสลง"
        return "JPY อ่อน → USDJPY มีโอกาสขึ้น / GBPJPY มีโอกาสขึ้น"

    if currency == "GBP":
        if direction == "strong":
            return "GBP แข็ง → GBPUSD มีโอกาสขึ้น / GBPJPY มีโอกาสขึ้น"
        return "GBP อ่อน → GBPUSD มีโอกาสลง / GBPJPY มีโอกาสลง"

    return ""


def build_rule_interpretation(news):
    currency = news["currency"]
    title = news["title"]
    news_type = classify_news(title)

    if news_type == "inflation":
        return f"""
        <b>ข่าวเงินเฟ้อ / ค่าแรง</b><br>
        ถ้า <b>Actual สูงกว่า Forecast</b> → เงินเฟ้อ/ค่าแรงร้อนกว่าคาด → ธนาคารกลางอาจคงดอกสูงหรือลดดอกช้าลง → {currency_effect_text(currency, "strong")}<br><br>
        ถ้า <b>Actual ต่ำกว่า Forecast</b> → เงินเฟ้อ/ค่าแรงเย็นลง → ธนาคารกลางอาจผ่อนคลายเร็วขึ้น → {currency_effect_text(currency, "weak")}
        """

    if news_type == "jobs_good_when_higher":
        return f"""
        <b>ข่าวจ้างงานที่ตัวเลขสูง = ดี</b><br>
        ถ้า <b>Actual สูงกว่า Forecast</b> → ตลาดแรงงานแข็งแรง → {currency_effect_text(currency, "strong")}<br><br>
        ถ้า <b>Actual ต่ำกว่า Forecast</b> → ตลาดแรงงานอ่อนกว่าคาด → {currency_effect_text(currency, "weak")}
        """

    if news_type == "jobs_bad_when_higher":
        return f"""
        <b>ข่าวแรงงานที่ตัวเลขสูง = แย่</b><br>
        ถ้า <b>Actual สูงกว่า Forecast</b> → ว่างงาน/ขอรับสวัสดิการมากกว่าคาด → {currency_effect_text(currency, "weak")}<br><br>
        ถ้า <b>Actual ต่ำกว่า Forecast</b> → ตลาดแรงงานดีกว่าคาด → {currency_effect_text(currency, "strong")}
        """

    if news_type == "growth":
        return f"""
        <b>ข่าวเศรษฐกิจ / การเติบโต / PMI / ยอดขาย</b><br>
        ถ้า <b>Actual สูงกว่า Forecast</b> → เศรษฐกิจแข็งแรงกว่าคาด → {currency_effect_text(currency, "strong")}<br><br>
        ถ้า <b>Actual ต่ำกว่า Forecast</b> → เศรษฐกิจอ่อนกว่าคาด → {currency_effect_text(currency, "weak")}
        """

    if news_type == "spending":
        return f"""
        <b>ข่าวรายได้/การใช้จ่ายผู้บริโภค</b><br>
        ถ้า <b>Actual สูงกว่า Forecast</b> → การใช้จ่ายแข็งแรง → {currency_effect_text(currency, "strong")}<br><br>
        ถ้า <b>Actual ต่ำกว่า Forecast</b> → การใช้จ่ายอ่อนลง → {currency_effect_text(currency, "weak")}
        """

    if news_type == "central_bank":
        if currency == "JPY":
            return """
            <b>ข่าว BOJ / ญี่ปุ่น</b><br>
            ถ้า BOJ พูด <b>hawkish</b> หรือส่งสัญญาณขึ้นดอก → JPY แข็ง → USDJPY / GBPJPY มีโอกาสลง<br><br>
            ถ้า BOJ พูด <b>dovish</b> หรือยังไม่รีบขึ้นดอก → JPY อ่อน → USDJPY / GBPJPY มีโอกาสขึ้น<br><br>
            ถ้าเป็นข่าวแทรกแซงค่าเงิน → มักเป็นการซื้อ JPY → USDJPY มีโอกาสร่วงแรง
            """

        if currency == "GBP":
            return """
            <b>ข่าว BOE / อังกฤษ</b><br>
            ถ้า BOE hawkish หรือขึ้นดอก/ลดดอกช้ากว่าคาด → GBP แข็ง → GBPUSD / GBPJPY มีโอกาสขึ้น<br><br>
            ถ้า BOE dovish หรือส่งสัญญาณลดดอก → GBP อ่อน → GBPUSD / GBPJPY มีโอกาสลง
            """

        return """
        <b>ข่าว Fed / FOMC / Powell</b><br>
        ถ้า Fed hawkish หรือดอกสูงนานกว่าคาด → USD แข็ง → USDJPY มีโอกาสขึ้น / GBPUSD มีโอกาสลง<br><br>
        ถ้า Fed dovish หรือส่งสัญญาณลดดอก → USD อ่อน → USDJPY มีโอกาสลง / GBPUSD มีโอกาสขึ้น
        """

    return f"""
    <b>ข่าวทั่วไป</b><br>
    โดยทั่วไปถ้า <b>Actual ดีกว่า Forecast</b> → {currency} มีโอกาสแข็ง<br><br>
    ถ้า <b>Actual แย่กว่า Forecast</b> → {currency} มีโอกาสอ่อน<br><br>
    ให้ดู Actual เทียบ Forecast เป็นหลัก และดูว่าตลาดรับรู้ล่วงหน้าไปแล้วหรือยัง
    """


# ================= FETCH NEWS =================

def fetch_forex_factory_news():
    try:
        response = requests.get(
            FOREX_FACTORY_XML,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()

        root = ET.fromstring(response.content)
        news = []

        for event in root.findall("event"):
            title = event.findtext("title", default="-")
            currency = event.findtext("country", default="-")
            date = event.findtext("date", default="-")
            time_text = event.findtext("time", default="-")
            impact = event.findtext("impact", default="-")
            actual = event.findtext("actual", default="-")
            forecast = event.findtext("forecast", default="-")
            previous = event.findtext("previous", default="-")

            if currency not in TARGET_CURRENCIES:
                continue

            if impact not in TARGET_IMPACTS:
                continue

            event_dt_ict = parse_event_datetime(date, time_text)

            item = {
                "date_raw": date,
                "time_raw": time_text,
                "date_ict": format_ict_date(event_dt_ict),
                "time_ict": format_ict_time(event_dt_ict),
                "datetime_ict": event_dt_ict,
                "currency": currency,
                "impact": impact,
                "title": title,
                "actual": actual or "-",
                "forecast": forecast or "-",
                "previous": previous or "-",
            }

            item["rule"] = build_rule_interpretation(item)
            news.append(item)

        news.sort(
            key=lambda x: x["datetime_ict"] or datetime.max.replace(tzinfo=ICT_TZ)
        )

        print(f"Fetched {len(news)} filtered news items")
        return news

    except Exception as e:
        print(f"Error fetching Forex Factory news: {e}")
        return []


# ================= FILTERS =================

def get_today_news(news):
    current = now_ict()
    today_news = []

    for n in news:
        dt = n["datetime_ict"]
        if dt and is_same_ict_day(dt, current):
            today_news.append(n)

    return today_news


def get_upcoming_news(news, min_minutes=8, max_minutes=15):
    current = now_ict()
    upcoming = []

    for n in news:
        dt = n["datetime_ict"]
        if not dt:
            continue

        minutes_left = (dt - current).total_seconds() / 60

        if min_minutes <= minutes_left <= max_minutes:
            n["minutes_left"] = round(minutes_left)
            upcoming.append(n)

    return upcoming


# ================= EMAIL HTML =================

def impact_badge(impact):
    if impact == "High":
        return """
        <span style="display:inline-block;background:#e74c3c;color:white;
        padding:5px 10px;border-radius:999px;font-size:12px;font-weight:bold;">
        🔴 HIGH
        </span>
        """

    return """
    <span style="display:inline-block;background:#f39c12;color:white;
    padding:5px 10px;border-radius:999px;font-size:12px;font-weight:bold;">
    🟠 MEDIUM
    </span>
    """


def build_news_cards(news, show_countdown=False):
    if not news:
        return """
        <div class="empty-card">
            ไม่มีข่าวที่เข้าเงื่อนไข
        </div>
        """

    cards = ""

    for n in news:
        countdown_html = ""
        if show_countdown:
            countdown_html = f"""
            <div class="countdown">
                ⏰ อีกประมาณ {n.get("minutes_left", "-")} นาที
            </div>
            """

        cards += f"""
        <div class="news-card">
            <div class="card-top">
                <div>
                    <div class="currency">{n["currency"]}</div>
                    <div class="event-title">{n["title"]}</div>
                </div>
                <div class="impact-wrap">
                    {impact_badge(n["impact"])}
                </div>
            </div>

            <div class="time-box">
                🕒 {n["date_ict"]} เวลา {n["time_ict"]} น. ไทย
                {countdown_html}
            </div>

            <div class="numbers">
                <div class="num-box">
                    <div class="num-label">Actual</div>
                    <div class="num-value">{n["actual"]}</div>
                </div>
                <div class="num-box">
                    <div class="num-label">Forecast</div>
                    <div class="num-value">{n["forecast"]}</div>
                </div>
                <div class="num-box">
                    <div class="num-label">Previous</div>
                    <div class="num-value">{n["previous"]}</div>
                </div>
            </div>

            <div class="rule-box">
                {n["rule"]}
            </div>
        </div>
        """

    return cards


def build_email(news, mode):
    current = now_ict()
    now_text = current.strftime("%d/%m/%Y %H:%M")

    high_count = sum(1 for n in news if n["impact"] == "High")
    medium_count = sum(1 for n in news if n["impact"] == "Medium")

    if mode == "morning":
        subject = (
            f"🌅 Forex Morning Brief | {now_text} ICT | "
            f"High {high_count} / Medium {medium_count}"
        )
        title = "🌅 Forex Morning Brief"
        subtitle = "รวมข่าว High / Medium Impact เฉพาะวันนี้ ตามเวลาไทย"
        show_countdown = False
    else:
        subject = (
            f"🚨 Forex Pre-News Alert | {now_text} ICT | "
            f"{len(news)} ข่าวใกล้ออก"
        )
        title = "🚨 Forex Pre-News Alert"
        subtitle = "แจ้งเตือนข่าวที่กำลังจะออกในอีกประมาณ 8–15 นาที"
        show_countdown = True

    cards_html = build_news_cards(news, show_countdown=show_countdown)

    html = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                margin: 0;
                padding: 16px;
                background: #f4f6f8;
                color: #111827;
                font-family: Arial, Helvetica, sans-serif;
            }}

            .container {{
                max-width: 720px;
                margin: 0 auto;
            }}

            .header {{
                background: #111827;
                color: white;
                border-radius: 16px;
                padding: 22px;
                margin-bottom: 16px;
            }}

            .header h1 {{
                margin: 0;
                font-size: 24px;
                line-height: 1.3;
            }}

            .header p {{
                margin: 8px 0 0;
                font-size: 14px;
                color: #d1d5db;
                line-height: 1.5;
            }}

            .summary {{
                background: white;
                border-radius: 14px;
                padding: 14px 18px;
                margin-bottom: 16px;
                font-size: 14px;
                color: #374151;
                box-shadow: 0 1px 6px rgba(0,0,0,0.06);
            }}

            .news-card {{
                background: white;
                border-radius: 16px;
                padding: 18px;
                margin-bottom: 16px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.08);
                border: 1px solid #e5e7eb;
            }}

            .card-top {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: flex-start;
            }}

            .currency {{
                display: inline-block;
                background: #eef2ff;
                color: #3730a3;
                font-size: 13px;
                font-weight: bold;
                padding: 4px 9px;
                border-radius: 999px;
                margin-bottom: 8px;
            }}

            .event-title {{
                font-size: 19px;
                font-weight: bold;
                color: #111827;
                line-height: 1.35;
            }}

            .impact-wrap {{
                white-space: nowrap;
                text-align: right;
            }}

            .time-box {{
                margin-top: 14px;
                background: #f9fafb;
                border-left: 4px solid #2563eb;
                padding: 10px 12px;
                border-radius: 8px;
                font-size: 14px;
                color: #374151;
                line-height: 1.5;
            }}

            .countdown {{
                margin-top: 6px;
                color: #dc2626;
                font-weight: bold;
            }}

            .numbers {{
                display: flex;
                gap: 10px;
                margin-top: 14px;
            }}

            .num-box {{
                flex: 1;
                background: #f3f4f6;
                border-radius: 12px;
                padding: 12px 10px;
                text-align: center;
            }}

            .num-label {{
                color: #6b7280;
                font-size: 12px;
                margin-bottom: 5px;
                text-transform: uppercase;
                letter-spacing: 0.4px;
            }}

            .num-value {{
                font-size: 18px;
                font-weight: bold;
                color: #111827;
            }}

            .rule-box {{
                margin-top: 14px;
                background: #fff8e1;
                border-left: 4px solid #f59e0b;
                padding: 12px 14px;
                border-radius: 8px;
                font-size: 14px;
                color: #374151;
                line-height: 1.65;
            }}

            .note {{
                background: white;
                border-radius: 14px;
                padding: 16px;
                margin-top: 18px;
                font-size: 14px;
                color: #4b5563;
                line-height: 1.6;
                box-shadow: 0 1px 6px rgba(0,0,0,0.06);
            }}

            .empty-card {{
                background: white;
                border-radius: 16px;
                padding: 20px;
                text-align: center;
                color: #6b7280;
                box-shadow: 0 1px 6px rgba(0,0,0,0.06);
            }}

            .footer {{
                text-align: center;
                color: #9ca3af;
                font-size: 12px;
                padding: 16px;
            }}

            @media only screen and (max-width: 600px) {{
                body {{
                    padding: 10px;
                }}

                .header h1 {{
                    font-size: 22px;
                }}

                .card-top {{
                    display: block;
                }}

                .impact-wrap {{
                    text-align: left;
                    margin-top: 10px;
                }}

                .numbers {{
                    display: block;
                }}

                .num-box {{
                    margin-bottom: 10px;
                }}
            }}
        </style>
    </head>

    <body>
        <div class="container">
            <div class="header">
                <h1>{title}</h1>
                <p>{subtitle}<br>Generated at {now_text} ICT</p>
            </div>

            <div class="summary">
                <b>สรุป:</b> ข่าวทั้งหมด {len(news)} รายการ · 🔴 High {high_count} · 🟠 Medium {medium_count}
            </div>

            {cards_html}

            <div class="note">
                <b>วิธีใช้:</b><br>
                1) ก่อนข่าวออก ให้ดู Forecast / Previous ไว้ก่อน<br>
                2) พอ Actual ออกแล้ว ให้เอาข่าวนั้นมาถาม ChatGPT ต่อ เช่น
                “USD CPI Actual สูงกว่า Forecast แบบนี้ USDJPY ควรขึ้นหรือลง”<br>
                3) Rule-based view เป็นกติกาเบื้องต้น ไม่ใช่คำแนะนำลงทุน
                และราคาจริงอาจสวนได้ถ้าตลาดรับรู้ข่าวล่วงหน้าแล้ว
            </div>

            <div class="footer">
                Forex News Alert System · Forex Factory Calendar · Free Version
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

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_SENDER, GMAIL_RECIPIENT, msg.as_string())

    print(f"Email sent successfully: {subject}")


# ================= MAIN =================

def main():
    current = now_ict()
    print("=" * 60)
    print(f"Starting Forex Alert at {current.strftime('%d/%m/%Y %H:%M')} ICT")
    print("=" * 60)

    all_news = fetch_forex_factory_news()

    is_manual_run = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    if is_manual_run:
        print("Manual run detected. Sending today's Morning Brief for testing.")
        news_to_send = get_today_news(all_news)
        subject, html = build_email(news_to_send, mode="morning")
        send_email(subject, html)
        return

    if current.hour == 7 and current.minute < 10:
        print("Morning Brief time detected.")
        news_to_send = get_today_news(all_news)
        subject, html = build_email(news_to_send, mode="morning")
        send_email(subject, html)
        return

    news_to_send = get_upcoming_news(all_news, min_minutes=8, max_minutes=15)

    if not news_to_send:
        print("No upcoming news in 8–15 minutes. No email sent.")
        return

    subject, html = build_email(news_to_send, mode="alert")
    send_email(subject, html)
    print("Pre-news alert sent")


if __name__ == "__main__":
    main()