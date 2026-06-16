#!/usr/bin/env python3
import json, smtplib, os, sys, time, re, urllib.parse, xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

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
    import requests
    from deep_translator import GoogleTranslator
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "requests", "deep-translator", "-q"])
    import yfinance as yf
    import requests
    from deep_translator import GoogleTranslator

DAUM_HEADERS = {
    'Referer': 'https://finance.daum.net/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/plain, */*'
}

def get_daum_price(code):
    """Daum Finance로 국내 종목 가격 조회 (Yahoo 실패 시 대체)"""
    try:
        url = f"https://finance.daum.net/api/quotes/A{code}"
        r = requests.get(url, headers=DAUM_HEADERS, timeout=10)
        d = r.json()
        price      = float(d.get('tradePrice') or d.get('closePrice') or 0)
        prev_price = float(d.get('prevClosingPrice') or price)
        change     = price - prev_price
        change_pct = (change / prev_price * 100) if prev_price else 0
        volume     = float(d.get('accTradeVolume') or 0)
        if price > 0:
            return {'price': price, 'change': change, 'change_pct': change_pct, 'volume': volume}
        return None
    except:
        return None

def is_korean(text):
    return bool(re.search('[가-힣]', text or ''))

def translate_ko(text):
    """영어 텍스트를 한국어로 번역. 이미 한국어면 빈 문자열 반환."""
    if not text or is_korean(text):
        return ''
    try:
        result = GoogleTranslator(source='auto', target='ko').translate(text[:500])
        return result or ''
    except:
        return ''

def fmt_news_date(dt):
    """datetime을 한국시간 'MM.DD' 형식으로 변환"""
    try:
        return dt.astimezone(KST).strftime('%m.%d')
    except:
        return ''

def select_recent(items, count=3, days=14):
    """뉴스를 최신순 정렬. 최근 days일 이내를 우선하고, 부족하면 그 이전 것으로 채운다.
    각 item에는 정렬용 '_dt'(datetime 또는 None)가 들어 있어야 한다."""
    now = datetime.now(timezone.utc)
    def key(it):
        dt = it.get('_dt')
        # 날짜 없는 항목은 맨 뒤로
        return dt if dt else datetime.min.replace(tzinfo=timezone.utc)
    dated   = [it for it in items if it.get('_dt')]
    undated = [it for it in items if not it.get('_dt')]
    dated.sort(key=key, reverse=True)
    recent = [it for it in dated if (now - it['_dt']).days < days]
    older  = [it for it in dated if (now - it['_dt']).days >= days]
    ordered = recent + older + undated   # 최근 → 오래된 → 날짜미상
    picked = ordered[:count]
    for it in picked:
        it.pop('_dt', None)
    return picked

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── 주가 데이터 ──────────────────────────────────────────────
def get_stock_data(ticker, name):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            latest, prev = hist.iloc[-1], hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
            price = latest["Close"]; prev_price = prev["Close"]
            change = price - prev_price
            change_pct = (change / prev_price * 100) if prev_price else 0
            return {"name": name, "ticker": ticker, "price": price,
                    "change": change, "change_pct": change_pct, "volume": latest["Volume"]}
    except:
        pass
    # Yahoo 실패 시 Daum Finance로 대체 (국내 종목만)
    if ticker.endswith(('.KS', '.KQ')):
        code = ticker.split('.')[0]
        daum_data = get_daum_price(code)
        if daum_data:
            return {"name": name, "ticker": ticker, **daum_data}
    return {"name": name, "ticker": ticker, "error": "no data"}

# ── Google News RSS (Yahoo 실패 시 대체) ─────────────────────
def get_korean_news_rss(query, count=3):
    """Google News RSS - 한국어 뉴스 (API 키 불필요)"""
    try:
        enc_q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={enc_q}&hl=ko&gl=KR&ceid=KR:ko"
        r = requests.get(url, headers=HEADERS, timeout=10)
        root = ET.fromstring(r.content)
        news = []
        for item in root.findall('.//item'):   # 전체 수집 후 최신순 선택
            title = item.findtext('title', '').strip()
            link  = item.findtext('link', '').strip()
            dt = None
            pd = item.findtext('pubDate', '')
            if pd:
                try:
                    dt = parsedate_to_datetime(pd)
                except:
                    pass
            source = ''
            if ' - ' in title:
                title, source = title.rsplit(' - ', 1)
            if title:
                news.append({'title': title, 'summary': '', 'link': link, 'publisher': source,
                             'date': fmt_news_date(dt) if dt else '', '_dt': dt})
        return select_recent(news, count)
    except:
        return []

# ── 뉴스 (제목 + 요약) ───────────────────────────────────────
def get_news(ticker, name='', count=3):
    try:
        stock = yf.Ticker(ticker)
        news_list = getattr(stock, 'news', None) or []
        result = []
        for a in news_list:  # 전체 수집 후 최신순 선택
            content = a.get('content', {}) or {}
            # 제목
            title = content.get('title') or a.get('title', '')
            # 요약
            summary = (content.get('summary') or
                       content.get('description') or
                       a.get('summary') or
                       a.get('description') or '')
            if summary and len(summary) > 300:
                summary = summary[:280] + '...'
            # 링크
            link = ((content.get('canonicalUrl') or {}).get('url') or
                    (content.get('clickThroughUrl') or {}).get('url') or
                    a.get('link', ''))
            # 출처
            pub = ((content.get('provider') or {}).get('displayName') or
                   a.get('publisher', ''))
            # 날짜
            dt = None
            try:
                p = content.get('pubDate') or content.get('displayTime') or ''
                if p:
                    dt = datetime.fromisoformat(str(p).replace('Z', '+00:00'))
                elif a.get('providerPublishTime'):
                    dt = datetime.fromtimestamp(a['providerPublishTime'], tz=timezone.utc)
            except:
                pass
            if title:
                result.append({'title': title, 'summary': summary, 'link': link, 'publisher': pub,
                               'date': fmt_news_date(dt) if dt else '', '_dt': dt})
        if result:
            return select_recent(result, count)
    except:
        pass
    # Yahoo 뉴스 없으면 Google News RSS 대체 (종목명 검색)
    if name:
        return get_korean_news_rss(name, count)
    return []

# ── ETF 구성 종목 (config에서 직접 읽기) ─────────────────────
# holdings_cache: ticker -> [{name, symbol, pct}, ...]
holdings_cache = {}
for _etf in portfolio.get('etfs', []):
    _h = _etf.get('holdings', [])
    if _h:
        holdings_cache[_etf['ticker']] = _h

def get_etf_holdings(ticker, top_n=10):
    """Config에 저장된 ETF 구성 종목 반환"""
    return holdings_cache.get(ticker, [])[:top_n]

# ── 포맷 헬퍼 ────────────────────────────────────────────────
def fmt_price(price, ticker):
    return f"{price:,.0f}원" if ticker.endswith((".KS",".KQ")) else f"${price:,.2f}"

def fmt_change(change, pct, ticker):
    arrow = "▲" if change >= 0 else "▼"
    color = "#e53e3e" if change >= 0 else "#3182ce"
    amt = f"{abs(change):,.0f}원" if ticker.endswith((".KS",".KQ")) else f"${abs(change):,.2f}"
    return f'<span style="color:{color}">{arrow} {amt} ({pct:+.2f}%)</span>'

def news_html(news_list):
    """뉴스를 헤드라인+요약+한국어번역 형태로 렌더링"""
    if not news_list:
        return "<p style='color:#aaa;font-size:12px;margin:4px 0 0'>뉴스 없음</p>"
    items = ""
    for n in news_list:
        title   = n.get('title', '')
        summary = n.get('summary', '')
        link    = n.get('link', '')
        pub     = n.get('publisher', '')
        date    = n.get('date', '')
        date_html = (f"<span style='color:#a0aec0;font-size:11px;margin-left:6px'>({date})</span>"
                     if date else "")

        # 영어 제목
        title_html = (f"<a href='{link}' style='color:#1a365d;text-decoration:none;font-weight:bold;font-size:13px'>{title}</a>"
                      if link else
                      f"<span style='font-weight:bold;font-size:13px;color:#1a365d'>{title}</span>")

        # 한국어 제목 번역
        title_ko = translate_ko(title)
        title_ko_html = (f"<div style='color:#2b6cb0;font-size:13px;font-weight:bold;margin-top:2px'>→ {title_ko}</div>"
                         if title_ko else "")

        # 영어 요약
        summary_html = (f"<div style='color:#718096;font-size:12px;margin-top:4px;line-height:1.5'>{summary}</div>"
                        if summary else "")

        # 한국어 요약 번역
        summary_ko = translate_ko(summary) if summary else ''
        summary_ko_html = (f"<div style='color:#4a5568;font-size:12px;margin-top:2px;line-height:1.5;background:#f7fafc;padding:4px 8px;border-radius:4px'>{summary_ko}</div>"
                           if summary_ko else "")

        pub_html = f"<div style='color:#a0aec0;font-size:11px;margin-top:4px'>{pub}</div>" if pub else ""

        items += f"""
        <div style='margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed #e2e8f0'>
          {title_html}{date_html}
          {title_ko_html}
          {summary_html}
          {summary_ko_html}
          {pub_html}
        </div>"""

    return f"<div style='margin-top:6px'>{items}</div>"

# ── 주식 섹션 빌더 ───────────────────────────────────────────
def build_stock_section(stocks_list, title, data_map, news_map):
    rows = ""
    for s in stocks_list:
        t, n = s["ticker"], s["name"]
        d = data_map.get(t)
        n_html = news_html(news_map.get(t, []))

        if not d or "error" in d:
            price_cell = "<span style='color:#bbb'>데이터 없음</span>"
        else:
            vol = f"{int(d['volume']):,}" if d.get("volume") else "-"
            price_cell = f"""
              <b style='font-size:15px'>{fmt_price(d['price'], t)}</b>
              &nbsp; {fmt_change(d['change'], d['change_pct'], t)}<br>
              <small style='color:#aaa'>거래량 {vol}</small>"""

        rows += f"""
        <tr style='border-top:1px solid #edf2f7'>
          <td style='padding:12px 10px;width:25%;vertical-align:top;background:#fafafa'>
            <div style='font-weight:bold;font-size:14px'>{n}</div>
            <div style='color:#888;font-size:11px;margin-bottom:6px'>{t}</div>
            {price_cell}
          </td>
          <td style='padding:12px 10px;vertical-align:top'>{n_html}</td>
        </tr>"""

    return f"""
    <h3 style='margin:28px 0 10px;color:#2d3748;border-bottom:2px solid #e2e8f0;padding-bottom:6px'>{title}</h3>
    <table width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;font-size:14px;border:1px solid #edf2f7;border-radius:8px'>
    <thead><tr style='background:#edf2f7'>
      <th style='text-align:left;padding:8px 10px;color:#718096;font-size:12px'>종목</th>
      <th style='text-align:left;padding:8px 10px;color:#718096;font-size:12px'>오늘의 헤드라인</th>
    </tr></thead>
    <tbody>{rows}</tbody></table>"""

# ── ETF 섹션 빌더 ────────────────────────────────────────────
def build_etf_section(etfs_list, data_map, holdings_map, holdings_news_map, news_map):
    rows = ""
    for s in etfs_list:
        t, n = s["ticker"], s["name"]
        d = data_map.get(t)
        holdings = holdings_map.get(t, [])

        if not d or "error" in d:
            price_str = "데이터 없음"
            change_str = ""
        else:
            price_str  = fmt_price(d['price'], t)
            change_str = fmt_change(d['change'], d['change_pct'], t)

        # 구성 종목 Top 3 뉴스 (없으면 ETF 자체 뉴스로 대체)
        top3 = holdings[:3]
        holdings_html = ""
        if not top3:
            etf_news = news_map.get(t, [])
            if etf_news:
                holdings_html = f"""
                <div style='margin-bottom:6px;font-size:11px;color:#a0aec0;font-style:italic'>
                  구성 종목 데이터 미제공 — ETF 자체 뉴스</div>
                {news_html(etf_news)}"""
            else:
                holdings_html = "<p style='color:#aaa;font-size:12px'>뉴스 없음</p>"
        else:
            for h in top3:
                sym  = h.get('symbol', '')
                news_key = sym if sym else h['name']
                h_news = news_html(holdings_news_map.get(news_key, []))
                pct_badge = (f"<span style='background:#ebf4ff;color:#2b6cb0;font-size:11px;"
                             f"padding:1px 6px;border-radius:10px;margin-left:6px'>{h['pct']}%</span>"
                             if h.get('pct') else "")
                sym_label = f"<span style='color:#aaa;font-size:11px;margin-left:4px'>{sym}</span>" if sym else ""
                holdings_html += f"""
                <div style='margin-bottom:14px'>
                  <div style='font-weight:bold;color:#2d3748;font-size:13px'>
                    {h['name']}{pct_badge}{sym_label}
                  </div>
                  {h_news}
                </div>"""

        # 나머지 구성 종목 목록 (4~10위)
        rest = holdings[3:10]
        rest_html = ""
        if rest:
            chips = " ".join(
                f"<span style='display:inline-block;background:#f7fafc;border:1px solid #e2e8f0;border-radius:4px;padding:2px 7px;font-size:11px;color:#4a5568;margin:2px'>"
                f"{h['name']} <b>{h['pct']}%</b></span>"
                for h in rest
            )
            rest_html = f"<div style='margin-top:8px'><span style='font-size:11px;color:#a0aec0'>기타 구성 종목: </span>{chips}</div>"

        rows += f"""
        <tr style='border-top:2px solid #e2e8f0'>
          <td colspan='2' style='padding:14px 12px 4px;background:#f7fafc'>
            <b style='font-size:15px;color:#1a365d'>{n}</b>
            <span style='color:#888;font-size:12px;margin-left:8px'>{t}</span>
            &nbsp;&nbsp;
            <b style='font-size:15px'>{price_str}</b>
            &nbsp; {change_str}
          </td>
        </tr>
        <tr>
          <td colspan='2' style='padding:10px 12px 16px'>
            <div style='font-size:12px;font-weight:bold;color:#718096;margin-bottom:8px'>비중 상위 3개 종목 뉴스</div>
            {holdings_html}
            {rest_html}
          </td>
        </tr>"""

    return f"""
    <h3 style='margin:28px 0 10px;color:#2d3748;border-bottom:2px solid #e2e8f0;padding-bottom:6px'>🗂 ETF</h3>
    <table width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;font-size:14px;border:1px solid #edf2f7'>
    <tbody>{rows}</tbody></table>"""

def build_html(idx_data, data_map, news_map, holdings_map, holdings_news_map):
    now = datetime.now(KST)
    idx_rows = ""
    for d in idx_data:
        if "error" in d:
            idx_rows += f"<tr><td><b>{d['name']}</b></td><td colspan='2' style='color:#bbb'>no data</td></tr>"
        else:
            idx_rows += (f"<tr><td><b>{d['name']}</b></td>"
                         f"<td style='text-align:right'>{d['price']:,.2f}</td>"
                         f"<td style='text-align:right'>{fmt_change(d['change'],d['change_pct'],d['ticker'])}</td></tr>")

    return f"""<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'></head>
<body style='font-family:Malgun Gothic,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:#f0f4f8'>
<div style='background:white;border-radius:12px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,0.08)'>
  <div style='background:linear-gradient(135deg,#1a365d,#2b6cb0);border-radius:8px;padding:20px;color:white;margin-bottom:24px'>
    <div style='font-size:12px;opacity:.8'>삼성증권 포트폴리오 브리핑</div>
    <div style='font-size:22px;font-weight:bold;margin-top:4px'>{now.strftime("%Y년 %m월 %d일")}</div>
  </div>

  <h3 style='margin:0 0 8px;color:#2d3748;border-bottom:2px solid #e2e8f0;padding-bottom:4px'>🌐 주요 지수</h3>
  <table width='100%' cellpadding='8' cellspacing='0' style='border-collapse:collapse;font-size:14px'>
  <thead><tr style='background:#f7fafc;color:#4a5568'>
    <th style='text-align:left'>지수</th><th style='text-align:right'>현재</th><th style='text-align:right'>등락</th>
  </tr></thead><tbody>{idx_rows}</tbody></table>

  {build_stock_section(portfolio['domestic_stocks'], '📈 국내 주식', data_map, news_map)}
  {build_etf_section(portfolio['etfs'], data_map, holdings_map, holdings_news_map, news_map)}
  {build_stock_section(portfolio['foreign_stocks'], '🌏 해외 주식', data_map, news_map)}

  <div style='margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;font-size:11px;color:#a0aec0;text-align:center'>
    {now.strftime("%Y-%m-%d %H:%M")} KST · Yahoo Finance
  </div>
</div></body></html>"""

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=== 포트폴리오 브리핑 시작 ===")

    print("[1/4] 지수 데이터 수집...")
    indices = [("^KS11","KOSPI"),("^KQ11","KOSDAQ"),("^GSPC","S&P 500"),("^IXIC","NASDAQ"),("^DJI","Dow Jones")]
    idx_data = [get_stock_data(t, n) for t, n in indices]

    print("[2/4] 종목 가격 및 뉴스 수집...")
    data_map = {}
    news_map = {}
    for s in all_stocks:
        t = s["ticker"]
        print(f"  {s['name']} ({t})...")
        data_map[t] = get_stock_data(t, s["name"])
        news_map[t] = get_news(t, s["name"], count=3)
        time.sleep(0.3)

    print("[3/4] ETF 구성 종목 Top3 뉴스 수집...")
    holdings_map      = {}
    holdings_news_map = {}
    for s in portfolio['etfs']:
        t = s["ticker"]
        print(f"  {s['name']} 구성 종목 조회...")
        holdings = get_etf_holdings(t, top_n=10)
        holdings_map[t] = holdings
        for h in holdings[:3]:   # 상위 3개만 뉴스 수집
            sym = h.get('symbol', '')
            news_key = sym if sym else h['name']
            if news_key not in holdings_news_map:
                print(f"    뉴스: {h['name']} ({news_key})...")
                holdings_news_map[news_key] = get_news(sym, h['name'], count=3)
                time.sleep(0.3)

    print("[4/4] 이메일 발송 중...")
    now = datetime.now(KST)
    subject = f"[포트폴리오 브리핑] {now.strftime('%Y.%m.%d')}"
    html = build_html(idx_data, data_map, news_map, holdings_map, holdings_news_map)

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_SENDER, EMAIL_RECIPIENT
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

    print(f"=== 완료 -> {EMAIL_RECIPIENT} ===")

if __name__ == "__main__":
    main()
