#!/usr/bin/env python3
import json, smtplib, os, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(script_dir, "briefing_config.json"), "r", encoding="utf-8") as f:
    config = json.load(f)

EMAIL_SENDER    = config["email"]["sender"]
EMAIL_PASSWORD  = config["email"]["app_password"]
EMAIL_RECIPIENT = config["email"]["recipient"]
portfolio       = config["portfolio"]
all_stocks      = portfolio["domestic_stocks"] + portfolio["etfs"] + portfolio["foreign_stocks"]

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "-q"])
    import yfinance as yf

def get_stock_data(ticker, name):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return {"name": name, "ticker": ticker, "error": "no data"}
        latest, prev = hist.iloc[-1], hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
        price = latest["Close"]; prev_price = prev["Close"]
        change = price - prev_price
        change_pct = (change / prev_price * 100) if prev_price else 0
        return {"name": name, "ticker": ticker, "price": price,
                "change": change, "change_pct": change_pct, "volume": latest["Volume"]}
    except Exception as e:
        return {"name": name, "ticker": ticker, "error": str(e)}

def fmt_price(price, ticker):
    return f"{price:,.0f}원" if ticker.endswith((".KS",".KQ")) else f"${price:,.2f}"

def fmt_change(change, pct, ticker):
    arrow = "▲" if change >= 0 else "▼"
    color = "#e53e3e" if change >= 0 else "#3182ce"
    amt = f"{abs(change):,.0f}원" if ticker.endswith((".KS",".KQ")) else f"${abs(change):,.2f}"
    return f'<span style="color:{color}">{arrow} {amt} ({pct:+.2f}%)</span>'

def build_section(stocks_list, title, data_map):
    rows = ""
    for s in stocks_list:
        t, n = s["ticker"], s["name"]
        d = data_map.get(t)
        if not d or "error" in d:
            rows += f"<tr><td>{n}<br><small style='color:#888;font-size:14px'>{t}</small></td><td colspan='3' style='color:#bbb;text-align:center'>데이터 없음</td></tr>"
        else:
            vol = f"{int(d['volume']):,}" if d.get("volume") else "-"
            rows += f"<tr><td>{n}<br><small style='color:#888;font-size:14px'>{t}</small></td><td style='text-align:right'>{fmt_price(d['price'],t)}</td><td style='text-align:right'>{fmt_change(d['change'],d['change_pct'],t)}</td><td style='text-align:right;color:#aaa'>{vol}</td></tr>"
    return f"""<h3 style='margin:24px 0 8px;color:#2d3748;border-bottom:2px solid #e2e8f0;padding-bottom:4px;font-size:20px'>{title}</h3>
<table width='100%' cellpadding='10' cellspacing='0' style='border-collapse:collapse;font-size:17px'>
<thead><tr style='background:#f7fafc;color:#4a5568'><th style='text-align:left'>종목</th><th style='text-align:right'>현재가</th><th style='text-align:right'>등락</th><th style='text-align:right'>거래량</th></tr></thead>
<tbody>{rows}</tbody></table>"""

def build_html(idx_data, data_map):
    now = datetime.now()
    idx_rows = ""
    for d in idx_data:
        if "error" in d:
            idx_rows += f"<tr><td><b>{d['name']}</b></td><td colspan='2' style='color:#bbb'>데이터 없음</td></tr>"
        else:
            idx_rows += f"<tr><td><b>{d['name']}</b></td><td style='text-align:right'>{d['price']:,.2f}</td><td style='text-align:right'>{fmt_change(d['change'],d['change_pct'],d['ticker'])}</td></tr>"
    return f"""<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'></head>
<body style='font-family:Malgun Gothic,sans-serif;max-width:680px;margin:0 auto;padding:20px;background:#f0f4f8'>
<div style='background:white;border-radius:12px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,0.08)'>
<div style='background:linear-gradient(135deg,#1a365d,#2b6cb0);border-radius:8px;padding:20px;color:white;margin-bottom:24px'>
<div style='font-size:15px;opacity:.8'>삼성증권 포트폴리오 브리핑</div>
<div style='font-size:28px;font-weight:bold;margin-top:4px'>{now.strftime('%Y년 %m월 %d일')}</div></div>
<h3 style='margin:0 0 8px;color:#2d3748;border-bottom:2px solid #e2e8f0;padding-bottom:4px;font-size:20px'>🌐 주요 지수</h3>
<table width='100%' cellpadding='10' cellspacing='0' style='border-collapse:collapse;font-size:17px'>
<thead><tr style='background:#f7fafc;color:#4a5568'><th style='text-align:left'>지수</th><th style='text-align:right'>현재</th><th style='text-align:right'>등락</th></tr></thead>
<tbody>{idx_rows}</tbody></table>
{build_section(portfolio['domestic_stocks'],'📈 국내 주식',data_map)}
{build_section(portfolio['etfs'],'🗂 ETF',data_map)}
{build_section(portfolio['foreign_stocks'],'🌏 해외 주식',data_map)}
<div style='margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;font-size:14px;color:#a0aec0;text-align:center'>
데이터 기준: {now.strftime('%Y-%m-%d %H:%M')} KST</div></div></body></html>"""

def main():
    print("데이터 수집 중...")
    indices = [("^KS11","KOSPI"),("^KQ11","KOSDAQ"),("^GSPC","S&P 500"),("^IXIC","NASDAQ"),("^DJI","다우존스")]
    idx_data = [get_stock_data(t, n) for t, n in indices]
    data_map = {}
    for s in all_stocks:
        print(f"  {s['name']} ({s['ticker']})...")
        data_map[s["ticker"]] = get_stock_data(s["ticker"], s["name"])

    now = datetime.now()
    subject = f"[포트폴리오 브리핑] {now.strftime('%Y.%m.%d')}"
    html = build_html(idx_data, data_map)

    print("이메일 발송 중...")
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_SENDER, EMAIL_RECIPIENT
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    print(f"완료 -> {EMAIL_RECIPIENT}")

if __name__ == "__main__":
    main()
