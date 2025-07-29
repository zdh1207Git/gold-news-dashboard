import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from fake_useragent import UserAgent
from datetime import datetime, timedelta
import time
import os
import csv
from pathlib import Path
import re
import html
from snownlp import SnowNLP

# --- 常量定义 ---
KEYWORDS = ["沪金", "黄金期货", "COMEX黄金", "实物黄金", "黄金ETF", "美联储", "利率"]
URL_TEMPLATE = "https://search.sina.com.cn/?q={keyword}&range=all&c=news&sort=time&page={page}"
DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "news_data.csv"
CRAWL_INTERVAL_HOURS = 4

# --- 请求头设置 ---
ua = UserAgent()
headers = {
    "User-Agent": ua.random
}

# --- 数据清洗与解析 ---

def ultimate_clean_text(raw_html: str) -> str:
    if not isinstance(raw_html, str):
        return ""
    text = raw_html
    while True:
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = BeautifulSoup(text, "html.parser").get_text(strip=True)
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_sina_time(time_str: str) -> datetime:
    if not isinstance(time_str, str):
        return datetime.now()
    now = datetime.now()
    try:
        if "分钟前" in time_str:
            minutes = int(re.search(r'(\d+)', time_str).group(1))
            return now - timedelta(minutes=minutes)
        elif "今天" in time_str:
            time_part = time_str.split(" ")[-1]
            return datetime.strptime(f"{now.strftime('%Y-%m-%d')} {time_part}", "%Y-%m-%d %H:%M")
        else:
            parsed_dt = datetime.strptime(f"{now.year}-{time_str}", "%Y-%m-%d %H:%M")
            if parsed_dt > now:
                parsed_dt = parsed_dt.replace(year=now.year - 1)
            return parsed_dt
    except (ValueError, AttributeError):
        return now

def get_sentiment_score(text: str) -> float:
    if not text:
        return 0.5
    return SnowNLP(text).sentiments

# --- 核心功能函数 (已恢复到简洁版本) ---
def fetch_news(keyword: str, pages: int = 2) -> list[dict]:
    news_list = []
    for page in range(1, pages + 1):
        url = URL_TEMPLATE.format(keyword=keyword, page=page)
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                results = soup.find_all("div", class_="box-result")
                for item in results:
                    if not isinstance(item, Tag):
                        continue
                    title_tag = item.find("h2")
                    summary_tag = item.find("p", class_="content")
                    time_tag = item.find("span", class_="fgray_time")
                    
                    title = ultimate_clean_text(title_tag.get_text() if title_tag else "")
                    summary = ultimate_clean_text(summary_tag.get_text() if summary_tag else "")

                    link_tag = title_tag.find("a") if title_tag else None
                    link = link_tag["href"] if link_tag else None
                    pub_time_str = time_tag.get_text(strip=True) if time_tag else None
                    parsed_time = parse_sina_time(pub_time_str)

                    if title and summary:
                        content_to_check = title + summary
                        sentiment = get_sentiment_score(content_to_check)
                        if any(kw in content_to_check for kw in KEYWORDS):
                            news_list.append({"title": title, "summary": summary, "time": parsed_time.isoformat(), "url": link, "sentiment": sentiment})
            time.sleep(1)
        except requests.RequestException as e:
            print(f"抓取页面 {url} 失败: {e}")
        except Exception as e:
            print(f"处理页面 {url} 时发生未知错误: {e}")
    return news_list

def save_news_to_csv(news_list: list[dict], filepath: Path) -> int:
    filepath.parent.mkdir(exist_ok=True)
    file_exists = filepath.exists()
    new_items_count = 0
    try:
        fieldnames = ["time", "title", "summary", "url", "sentiment"]
        with open(filepath, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            existing_urls = set()
            if file_exists:
                with open(filepath, mode='r', newline='', encoding='utf-8-sig') as fr:
                    reader = csv.DictReader(fr)
                    for row in reader:
                        if 'url' in row:
                            existing_urls.add(row['url'])
            new_items = [item for item in news_list if item.get('url') not in existing_urls]
            if new_items:
                writer.writerows(new_items)
                new_items_count = len(new_items)
    except IOError as e:
        print(f"无法写入文件 {filepath}: {e}")
    return new_items_count

# --- 可被外部调用的主函数 ---
def run_news_crawl():
    if DATA_FILE.exists():
        last_modified_time = datetime.fromtimestamp(DATA_FILE.stat().st_mtime)
        time_since_last_crawl = datetime.now() - last_modified_time
        if time_since_last_crawl < timedelta(hours=CRAWL_INTERVAL_HOURS):
            remaining_time = timedelta(hours=CRAWL_INTERVAL_HOURS) - time_since_last_crawl
            hours, remainder = divmod(remaining_time.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return f"还没到4小时，请在 {hours} 小时 {minutes} 分钟后重试。", 0
    
    print("开始获取新数据...")
    all_news = []
    for keyword in KEYWORDS:
        print(f"--- 正在处理关键词: {keyword} ---")
        news = fetch_news(keyword, pages=2)
        all_news.extend(news)
    
    if not all_news:
        return "未能抓取到任何新闻。", 0

    unique_news = list({item["url"]: item for item in all_news if item.get("url")}.values())
    saved_count = save_news_to_csv(unique_news, DATA_FILE)
    
    if saved_count > 0:
        return f"任务完成，成功获取 {saved_count} 条新新闻！", saved_count
    else:
        return "任务完成，但未发现任何新内容。", 0

# --- 当作为独立脚本运行时 --- 
if __name__ == "__main__":
    message, count = run_news_crawl()
    print(message)
