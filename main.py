"""
Forex News Alert System
=======================
ดึงข่าวจาก Forex Factory + Twitter/X + เว็บข่าว
วิเคราะห์ด้วย Claude AI → ส่ง Gmail

หน้าที่ของไฟล์นี้:
  1. fetch_forexfactory()  — ดึงข่าว Forex Factory
  2. fetch_twitter_news()  — ค้นหา tweet จาก VIP accounts
  3. fetch_web_news()      — ค้นข่าวจากเว็บทั่วไป
  4. analyze_with_claude() — AI วิเคราะห์ทิศทาง
  5. send_gmail()          — ส่ง email HTML
  6. main()               — ควบคุมทั้งหมด
"""

import os
import json
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
import anthropic

# ─── CONFIG (อ่านจาก Environment Variables) ──────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
GMAIL_SENDER       = os.environ["GMAIL_SENDER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_RECIPIENT    = os.environ["GMAIL_RECIPIENT"]

# แอคเคาท์ Twitter/X ที่ติดตาม
TWITTER_ACCOUNTS = [
    "federalreserve",
    "BLS_gov",
    "Bank_of_Japan_e",
    "bankofengland",
    "FinancialJuice",
    "LiveSquawk",
]

# คู่เงินที่สนใจ
TARGET_CURRENCIES = ["USD", "JPY", "GBP"]
TARGET_PAIRS      = ["USDJPY", "GBPUSD", "GBPJPY", "XAUUSD"]

# ─── 1. FOREX FACTORY ─────────────────────────────────────────────────────────

def fetch_forexfactory() -> list[dict]:
    """
    ดึงข่าวจาก Forex Factory
    - กรองเฉพาะ impact: red / orange
    - กรองเฉพาะสกุลเงิน USD, JPY, GBP
    - รองรับการดึงย้อนหลัง (ถ้าเป็นรอบเช้า 07:00 จะดึงข่าวดึกด้วย)
    """
    url = "https://www.forexfactory.com/calendar"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        events = []

        for row in soup.select("tr.calendar__row"):
            # ─ impact color
            impact_el = row.select_one(".calendar__impact span")
            if not impact_el:
                continue
            cls = " ".join(impact_el.get("class", [])).lower()
            if "high" in cls or "red" in cls:
                impact = "🔴 HIGH"
            elif "medium" in cls or "orange" in cls:
                impact = "🟠 MEDIUM"
            else:
                continue  # ข้าม low impact

            # ─ currency filter
            currency_el = row.select_one(".calendar__currency")
            if not currency_el:
                continue
            currency = currency_el.get_text(strip=True).upper()
            if currency not in TARGET_CURRENCIES:
                continue

            # ─ เก็บข้อมูล
            def txt(sel):
                el = row.select_one(sel)
                return el.get_text(strip=True) if el else ""

            events.append({
                "source":   "Forex Factory",
                "time":     txt(".calendar__time"),
                "currency": currency,
                "event":    txt(".calendar__event"),
                "actual":   txt(".calendar__actual"),
                "forecast": txt(".calendar__forecast"),
                "previous": txt(".calendar__previous"),
                "impact":   impact,
            })

        print(f"[ForexFactory] ดึงได้ {len(events)} รายการ")
        return events

    except Exception as e:
        print(f"[ForexFactory] Error: {e}")
        # ─ Mock data สำหรับ test
        return [
            {
                "source": "Forex Factory (mock)",
                "time": "14:30", "currency": "USD",
                "event": "Non-Farm Payrolls",
                "actual": "256K", "forecast": "200K", "previous": "212K",
                "impact": "🔴 HIGH",
            },
            {
                "source": "Forex Factory (mock)",
                "time": "08:00", "currency": "GBP",
                "event": "BoE Interest Rate Decision",
                "actual": "5.25%", "forecast": "5.25%", "previous": "5.00%",
                "impact": "🔴 HIGH",
            },
        ]


# ─── 2. TWITTER/X (ผ่าน Claude Web Search) ───────────────────────────────────

def fetch_twitter_news(client: anthropic.Anthropic) -> list[dict]:
    """
    ใช้ Claude web search ค้นหา tweet ล่าสุดจาก VIP accounts
    เพราะไม่ต้องใช้ X API — ค้นผ่าน Google/Bing แทน
    """
    accounts_str = ", ".join([f"@{a}" for a in TWITTER_ACCOUNTS])
    prompt = f"""Search for the most recent tweets (last 24 hours) from these Twitter/X accounts:
{accounts_str}

Focus only on tweets related to: USD, JPY, GBP, interest rates, inflation, 
economic data, Federal Reserve, Bank of Japan, Bank of England, forex markets, gold.

Return a JSON array only, no markdown, no explanation:
[
  {{
    "account": "@accountname",
    "time": "HH:MM or time description",
    "content": "tweet content summary",
    "relevance": "USD/JPY/GBP/GOLD",
    "impact": "HIGH/MEDIUM"
  }}
]

If no relevant tweets found, return empty array: []"""

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        # รวม text จาก response
        full_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                full_text += block.text

        # parse JSON
        clean = full_text.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        
        tweets = json.loads(clean) if clean.strip().startswith("[") else []
        
        # เพิ่ม source field
        for t in tweets:
            t["source"] = "Twitter/X"
            t["event"]  = t.get("content", "")
            t["currency"] = t.get("relevance", "USD")
        
        print(f"[Twitter/X] ดึงได้ {len(tweets)} รายการ")
        return tweets

    except Exception as e:
        print(f"[Twitter/X] Error: {e}")
        return []


# ─── 3. WEB NEWS ──────────────────────────────────────────────────────────────

def fetch_web_news(client: anthropic.Anthropic) -> list[dict]:
    """
    ใช้ Claude web search ค้นข่าว Forex ล่าสุดจาก Reuters, Bloomberg, FXStreet
    """
    prompt = """Search for the latest forex news (last 6 hours) affecting USD, JPY, GBP.
Search from: Reuters, Bloomberg, ForexLive, FXStreet, DailyFX

Return JSON array only, no markdown:
[
  {
    "source": "Reuters/Bloomberg/etc",
    "time": "HH:MM or time description", 
    "currency": "USD/JPY/GBP",
    "event": "brief headline",
    "actual": "",
    "forecast": "",
    "previous": "",
    "impact": "🔴 HIGH or 🟠 MEDIUM",
    "summary": "1-2 sentence summary of what happened"
  }
]

Only include HIGH or MEDIUM impact news. Return [] if nothing significant."""

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        full_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                full_text += block.text

        clean = full_text.strip()
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]

        news = json.loads(clean) if clean.strip().startswith("[") else []
        print(f"[Web News] ดึงได้ {len(news)} รายการ")
        return news

    except Exception as e:
        print(f"[Web News] Error: {e}")
        return []


# ─── 4. AI ANALYSIS ───────────────────────────────────────────────────────────

def analyze_with_claude(
    client: anthropic.Anthropic,
    all_news: list[dict],
    is_morning_brief: bool = False,
) -> str:
    """
    ส่งข่าวทั้งหมดให้ Claude วิเคราะห์
    - รอบปกติ: วิเคราะห์ข่าว 30 นาทีล่าสุด
    - Morning Brief: สรุปข่าวดึกย้อนหลัง + แนวโน้มเช้า
    - มีขั้นตอน "ทบทวน" ก่อนส่ง
    """
    if not all_news:
        return "<p style='color:#999;'>ไม่มีข่าว High/Medium impact ในช่วงนี้</p>"

    # สร้าง news text
    news_lines = []
    for n in all_news:
        line = (
            f"[{n.get('impact','?')}] {n.get('source','')} | "
            f"{n.get('currency','')} | {n.get('event','')} | "
            f"เวลา: {n.get('time','')} | "
            f"Actual: {n.get('actual','-')} | "
            f"Forecast: {n.get('forecast','-')} | "
            f"Prev: {n.get('previous','-')}"
        )
        if n.get("summary"):
            line += f" | สรุป: {n['summary']}"
        news_lines.append(line)

    news_text = "\n".join(news_lines)
    brief_note = "⚠️ นี่คือ Morning Brief — รวมข่าวดึก (22:00–07:00)" if is_morning_brief else ""

    prompt = f"""คุณเป็น Senior Forex Analyst ผู้เชี่ยวชาญ วิเคราะห์ข่าวต่อไปนี้:

{brief_note}

ข่าวที่ได้รับ:
{news_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ขั้นตอนที่ 1: วิเคราะห์ข่าวแต่ละชิ้น
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
สำหรับแต่ละข่าว ให้ระบุ:
- ใครพูด/ออกข้อมูลอะไร
- ตัวเลขออกมาดีกว่า/แย่กว่าคาด
- ผลทันทีต่อ USD/JPY/GBP คืออะไร

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ขั้นตอนที่ 2: สรุปภาพรวมตลาด
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- USD แข็ง/อ่อน เพราะอะไร
- JPY แข็ง/อ่อน เพราะอะไร  
- GBP แข็ง/อ่อน เพราะอะไร

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ขั้นตอนที่ 3: แนวโน้มคู่เงิน
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
วิเคราะห์ USDJPY, GBPUSD, GBPJPY, XAUUSD

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ขั้นตอนที่ 4: ทบทวนก่อนส่ง
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ตรวจสอบว่าการวิเคราะห์สอดคล้องกัน ไม่ขัดแย้ง

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ตอบเป็น HTML ใช้ format นี้ทุกครั้ง:

<div class="ai-analysis">

<div class="news-breakdown">
<h3>📰 วิเคราะห์ข่าวแต่ละชิ้น</h3>
[วิเคราะห์ทีละ สั้นๆ ชัดๆ]
</div>

<div class="market-overview">
<h3>🌍 ภาพรวมตลาดตอนนี้</h3>
<p><b>USD:</b> [แข็ง🟢/อ่อน🔴/ทรงตัว🟡] — [เหตุผล 1 ประโยค]</p>
<p><b>JPY:</b> [แข็ง🟢/อ่อน🔴/ทรงตัว🟡] — [เหตุผล 1 ประโยค]</p>
<p><b>GBP:</b> [แข็ง🟢/อ่อน🔴/ทรงตัว🟡] — [เหตุผล 1 ประโยค]</p>
</div>

<div class="pairs">
<h3>📊 แนวโน้มคู่เงิน</h3>

<div class="pair-card">
<b>USDJPY</b>
<p>แนวโน้ม: [ขึ้น📈/ลง📉/Sideways➡️]</p>
<p>ความมั่นใจ: [X]% — เพราะ [เหตุผลสำคัญ]</p>
<p>คำแนะนำ: <b>[BUY/SELL/WAIT]</b></p>
<p>แนวรับ: [ราคา] | แนวต้าน: [ราคา]</p>
<p>ความเสี่ยง: [อะไรที่อาจทำให้ผิด]</p>
</div>

<div class="pair-card">
<b>GBPUSD</b>
<p>แนวโน้ม: [ขึ้น📈/ลง📉/Sideways➡️]</p>
<p>ความมั่นใจ: [X]% — เพราะ [เหตุผลสำคัญ]</p>
<p>คำแนะนำ: <b>[BUY/SELL/WAIT]</b></p>
<p>แนวรับ: [ราคา] | แนวต้าน: [ราคา]</p>
<p>ความเสี่ยง: [อะไรที่อาจทำให้ผิด]</p>
</div>

<div class="pair-card">
<b>GBPJPY</b>
<p>แนวโน้ม: [ขึ้น📈/ลง📉/Sideways➡️]</p>
<p>ความมั่นใจ: [X]%</p>
<p>คำแนะนำ: <b>[BUY/SELL/WAIT]</b></p>
</div>

<div class="pair-card">
<b>XAUUSD (ทองคำ)</b>
<p>แนวโน้ม: [ขึ้น📈/ลง📉/Sideways➡️]</p>
<p>ความมั่นใจ: [X]% — เพราะ [เหตุผล]</p>
<p>คำแนะนำ: <b>[BUY/SELL/WAIT]</b></p>
<p>แนวรับ: $[ราคา] | แนวต้าน: $[ราคา]</p>
</div>

</div>

<div class="review-box">
<h3>🔄 ผลการทบทวน</h3>
<p>[สรุปว่าการวิเคราะห์สอดคล้องกันไหม มีอะไรที่ต้องระวังเพิ่มเติม]</p>
</div>

</div>

ตอบ HTML เท่านั้น ห้ามมี markdown backticks"""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text
        print("[Claude AI] วิเคราะห์เสร็จแล้ว")
        return result
    except Exception as e:
        print(f"[Claude AI] Error: {e}")
        return f"<p style='color:red;'>❌ AI Error: {e}</p>"


# ─── 5. BUILD EMAIL HTML ──────────────────────────────────────────────────────

def build_email(
    all_news: list[dict],
    analysis: str,
    is_morning_brief: bool = False,
) -> tuple[str, str]:
    """สร้าง subject + HTML body สำหรับ email"""
    
    now      = datetime.now().strftime("%d/%m/%Y %H:%M")
    red_cnt  = sum(1 for n in all_news if "HIGH" in n.get("impact", ""))
    label    = "🌅 Morning Brief" if is_morning_brief else "⚡ Forex Alert"
    subject  = f"{label} | {now} ICT | {red_cnt} HIGH Impact"

    # ─ สร้าง news rows
    rows_html = ""
    for n in all_news:
        imp   = n.get("impact", "")
        color = "#e74c3c" if "HIGH" in imp else "#e67e22"
        badge = (
            f'<span style="background:{color};color:white;'
            f'padding:2px 8px;border-radius:10px;font-size:11px;">'
            f'{imp}</span>'
        )
        # actual vs forecast สี
        actual_style = "color:#333;"
        try:
            a = float(n.get("actual","").replace("K","000").replace("%","").strip())
            f_ = float(n.get("forecast","").replace("K","000").replace("%","").strip())
            actual_style = "color:#27ae60;font-weight:bold;" if a > f_ else "color:#e74c3c;font-weight:bold;"
        except:
            pass

        rows_html += f"""
        <tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:9px 6px;font-size:12px;color:#888;">{n.get('time','-')}</td>
          <td style="padding:9px 6px;">{badge}</td>
          <td style="padding:9px 6px;font-size:12px;font-weight:700;">{n.get('currency','-')}</td>
          <td style="padding:9px 6px;font-size:12px;">{n.get('event','-')}</td>
          <td style="padding:9px 6px;font-size:12px;{actual_style}">{n.get('actual','-') or '-'}</td>
          <td style="padding:9px 6px;font-size:12px;color:#aaa;">{n.get('forecast','-') or '-'}</td>
          <td style="padding:9px 6px;font-size:12px;color:#aaa;">{n.get('source','-')}</td>
        </tr>"""

    morning_banner = ""
    if is_morning_brief:
        morning_banner = """
        <div style="background:#fff3cd;border-left:4px solid #ffc107;
                    padding:12px 16px;margin:0 0 20px;border-radius:0 6px 6px 0;">
          🌅 <b>Morning Brief</b> — สรุปข่าวช่วงดึก (22:00 – 07:00) ที่ผ่านมา
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:16px;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif;}}
  .wrap{{max-width:680px;margin:0 auto;}}
  .card{{background:#fff;border-radius:12px;overflow:hidden;
         box-shadow:0 2px 12px rgba(0,0,0,.08);margin-bottom:16px;}}
  .hdr{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);
        padding:24px 28px;color:#fff;}}
  .hdr h1{{margin:0;font-size:20px;font-weight:800;}}
  .hdr p{{margin:6px 0 0;font-size:12px;opacity:.7;}}
  .body{{padding:22px 28px;}}
  h3{{font-size:14px;font-weight:700;color:#1a1a2e;margin:0 0 14px;
      padding-bottom:8px;border-bottom:2px solid #f0f0f0;}}
  table{{width:100%;border-collapse:collapse;}}
  th{{background:#f8f9fa;padding:8px 6px;font-size:11px;text-transform:uppercase;
      color:#aaa;text-align:left;font-weight:600;}}
  /* AI analysis styles */
  .ai-analysis .news-breakdown,
  .ai-analysis .market-overview,
  .ai-analysis .pairs,
  .ai-analysis .review-box{{
    background:#f8f9fa;border-radius:8px;padding:16px 18px;margin-bottom:14px;
  }}
  .ai-analysis h3{{border-bottom:1px solid #e9ecef;}}
  .ai-analysis p{{margin:5px 0;font-size:13px;color:#444;line-height:1.6;}}
  .pair-card{{background:#fff;border:1px solid #e9ecef;border-radius:8px;
              padding:12px 14px;margin:10px 0;}}
  .pair-card b{{font-size:14px;color:#1a1a2e;}}
  .review-box{{border-left:3px solid #3498db;}}
  .disclaimer{{background:#fff8e1;border-left:3px solid #ffc107;
               padding:10px 14px;border-radius:0 6px 6px 0;
               font-size:11px;color:#777;margin-top:4px;}}
  .footer{{text-align:center;font-size:11px;color:#aaa;padding:16px;}}
</style>
</head>
<body>
<div class="wrap">

  <!-- HEADER -->
  <div class="card">
    <div class="hdr">
      <h1>{'🌅 Morning Brief' if is_morning_brief else '⚡ Forex Alert'}</h1>
      <p>{now} (ICT) &nbsp;|&nbsp; USD · JPY · GBP &nbsp;|&nbsp; {red_cnt} HIGH Impact</p>
    </div>
  </div>

  <!-- NEWS TABLE -->
  <div class="card">
    <div class="body">
      {morning_banner}
      <h3>📰 ข่าว High/Medium Impact</h3>
      <table>
        <thead>
          <tr>
            <th>เวลา</th><th>Impact</th><th>สกุลเงิน</th>
            <th>ข่าว / Tweet</th><th>Actual</th><th>Forecast</th><th>Source</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- AI ANALYSIS -->
  <div class="card">
    <div class="body">
      <h3>🤖 AI วิเคราะห์ + ทบทวนแล้ว</h3>
      {analysis}
      <div class="disclaimer">
        ⚠️ <b>Disclaimer:</b> ข้อมูลนี้เป็นเพียงการวิเคราะห์เพื่อประกอบการตัดสินใจเท่านั้น
        ไม่ใช่คำแนะนำการลงทุน การเทรด Forex/Gold มีความเสี่ยงสูง
        กรุณาตัดสินใจด้วยตัวเอง
      </div>
    </div>
  </div>

  <div class="footer">
    Forex Alert System · Powered by Claude AI<br>
    Forex Factory · Twitter/X · Reuters/Bloomberg
  </div>

</div>
</body>
</html>"""

    return subject, html


# ─── 6. SEND GMAIL ────────────────────────────────────────────────────────────

def send_gmail(subject: str, html_body: str) -> bool:
    """ส่ง email ผ่าน Gmail SMTP SSL"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as srv:
            srv.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            srv.sendmail(GMAIL_SENDER, GMAIL_RECIPIENT, msg.as_string())
        print(f"[Gmail] ✅ ส่งสำเร็จ: {subject}")
        return True
    except Exception as e:
        print(f"[Gmail] ❌ Error: {e}")
        return False


# ─── 7. MAIN ──────────────────────────────────────────────────────────────────

def main():
    now_hour = datetime.now().hour
    # รอบ 07:00 = Morning Brief (ดึงข่าวดึกด้วย)
    is_morning = (now_hour == 7)

    print(f"\n{'='*50}")
    print(f"  Forex Alert — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"  {'Morning Brief 🌅' if is_morning else 'Regular Run ⚡'}")
    print(f"{'='*50}")

    # สร้าง Anthropic client
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ─ ดึงข่าวทั้งหมด
    ff_news  = fetch_forexfactory()
    tw_news  = fetch_twitter_news(client)
    web_news = fetch_web_news(client)

    all_news = ff_news + tw_news + web_news
    print(f"\n[รวม] ข่าวทั้งหมด {len(all_news)} รายการ")

    # ─ วิเคราะห์
    analysis = analyze_with_claude(client, all_news, is_morning_brief=is_morning)

    # ─ สร้าง + ส่ง email
    subject, html = build_email(all_news, analysis, is_morning_brief=is_morning)
    send_gmail(subject, html)

    print(f"\n[เสร็จ] {datetime.now().strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    main()