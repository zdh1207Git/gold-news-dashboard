import streamlit as st
import pandas as pd
from pathlib import Path
import textwrap
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import html
import yfinance as yf
import requests
import plotly.graph_objects as go

from get_news import run_news_crawl

# --- 页面配置 ---
st.set_page_config(
    page_title="黄金市场洞察",
    page_icon="🥇",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 数据文件路径 ---
DATA_FILE = Path("data") / "news_data.csv"

# --- 自定义 CSS ---
st.markdown("""
<style>
    .card {
        background-color: #2E2E2E;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid #444;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        position: relative;
    }
    .news-title {
        font-size: 1.1em;
        font-weight: bold;
        color: #1E90FF;
        margin-bottom: 10px;
    }
    .sentiment-badge {
        position: absolute;
        top: 15px;
        right: 15px;
        padding: 5px 10px;
        border-radius: 15px;
        font-size: 0.8em;
        font-weight: bold;
        color: white;
    }
    .bullish { background-color: #28a745; }
    .bearish { background-color: #dc3545; }
    .neutral { background-color: #6c757d; }
</style>
""", unsafe_allow_html=True)

# --- 数据获取与处理 ---

def get_comex_gold_from_sina():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            'Referer': 'http://finance.sina.com.cn/'
        }
        response = requests.get("http://hq.sinajs.cn/list=hf_GC", headers=headers, timeout=5)
        response.raise_for_status()
        price_str = response.text.split(',')[1]
        return float(price_str)
    except (requests.RequestException, IndexError, ValueError) as e:
        print(f"从新浪财经获取COMEX黄金价格失败: {e}")
        return None

@st.cache_data(ttl=900)
def get_market_data():
    gold_price = cny_rate = None
    gold_price = get_comex_gold_from_sina()
    if gold_price is None:
        try:
            gold_ticker = yf.Ticker("GC=F")
            data = gold_ticker.history(period="1d", interval="1m")
            if not data.empty:
                gold_price = data['Close'].iloc[-1]
        except Exception as e:
            print(f"备用源 yfinance (GC=F) 也失败了: {e}")
    try:
        cny_ticker = yf.Ticker("CNY=X")
        data = cny_ticker.history(period="1d", interval="1m")
        if not data.empty:
            cny_rate = data['Close'].iloc[-1]
    except Exception as e:
        print(f"未能获取美元汇率 (CNY=X): {e}")
    return gold_price, cny_rate

@st.cache_data(ttl=3600)
def get_historical_data(days=90):
    try:
        tickers = yf.Tickers('GC=F CNY=X')
        hist = tickers.history(period=f"{days}d", auto_adjust=False)
        df = hist['Close'].copy()
        df.columns = ['COMEX_Gold', 'USD_CNY']
        df.ffill(inplace=True)
        df.dropna(inplace=True)
        df['Theoretical_Price'] = (df['COMEX_Gold'] / 31.1035) * df['USD_CNY']
        return df
    except Exception as e:
        st.error(f"获取历史数据失败: {e}")
        return pd.DataFrame()

# --- 图表创建函数 ---
def create_price_chart(df):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Theoretical_Price'], name='COMEX换算价', line=dict(color='#1E90FF')))
    fig.update_layout(title='COMEX黄金人民币换算价历史走势 (元/克)', template='plotly_dark', height=400,
                      margin=dict(l=20, r=20, t=40, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return fig

def create_sentiment_charts(df):
    sentiment_counts = df['sentiment_category'].value_counts()
    pie_fig = go.Figure(data=[go.Pie(
        labels=sentiment_counts.index,
        values=sentiment_counts.values,
        hole=.3,
        marker_colors=['#28a745','#dc3545','#6c757d']
    )])
    pie_fig.update_layout(title_text='新闻情绪分布', template='plotly_dark', showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
    daily_sentiment = df.groupby(df['time'].dt.date)['sentiment'].mean().reset_index()
    bar_fig = go.Figure()
    bar_fig.add_trace(go.Bar(
        x=daily_sentiment['time'],
        y=daily_sentiment['sentiment'],
        marker_color=daily_sentiment['sentiment'].apply(lambda s: '#28a745' if s > 0.6 else ('#dc3545' if s < 0.4 else '#6c757d'))
    ))
    bar_fig.update_layout(title_text='每日平均情绪趋势', template='plotly_dark', showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
    bar_fig.add_hline(y=0.5, line_width=1, line_dash="dash", line_color="gray")
    return pie_fig, bar_fig

@st.cache_data(ttl=600)
def load_data(filepath: Path) -> pd.DataFrame:
    if not filepath.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(filepath)
        df['time'] = pd.to_datetime(df['time'])
        df.dropna(subset=['title', 'summary', 'url', 'time'], inplace=True)
        df.drop_duplicates(subset=['url'], inplace=True)
        if 'sentiment' in df.columns:
            def categorize(score):
                if score > 0.6: return "利好"
                if score < 0.4: return "利空"
                return "中性"
            df['sentiment_category'] = df['sentiment'].apply(categorize)
        return df
    except Exception as e:
        st.error(f"加载新闻数据时出错: {e}")
        return pd.DataFrame()

# --- 主应用界面 ---
st.title("🥇 黄金市场洞察平台")

# --- 侧边栏 --- 
st.sidebar.header("⚙️ 控制与筛选")

# 将刷新按钮移动到侧边栏
if st.sidebar.button("🔄 刷新新闻"):
    with st.spinner("正在后台获取最新新闻，请稍候..."):
        message, count = run_news_crawl()
        if count > 0:
            st.toast(f"✅ {message}", icon="🎉")
        else:
            st.toast(f"ℹ️ {message}", icon="⏱️")
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---_---")

# --- 实时价格展示 ---
st.markdown("---_---")
gold_price, cny_rate = get_market_data()
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(label="COMEX 黄金 (GC=F)", value=f"${gold_price:.2f}" if gold_price else "N/A", help="数据来源: 新浪财经/Yahoo Finance")
with col2:
    st.metric(label="美元/离岸人民币 (CNY=X)", value=f"¥{cny_rate:.4f}" if cny_rate else "N/A", help="数据来源: Yahoo Finance")
theoretical_price = (gold_price / 31.1035) * cny_rate if gold_price and cny_rate else 0
with col3:
    st.metric(label="COMEX人民币换算价", value=f"¥{theoretical_price:.2f}" if theoretical_price else "N/A", help="公式: (COMEX价 / 31.1035) * 汇率")
st.markdown("---_---")

hist_df = get_historical_data()
if not hist_df.empty:
    fig = create_price_chart(hist_df)
    st.plotly_chart(fig, use_container_width=True)

df = load_data(DATA_FILE)

if not df.empty and 'sentiment' in df.columns:
    st.markdown("### 市场情绪仪表盘")
    pie_fig, bar_fig = create_sentiment_charts(df)
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(pie_fig, use_container_width=True)
    with col2:
        st.plotly_chart(bar_fig, use_container_width=True)

st.markdown("### 财经新闻速递")

if df.empty:
    st.warning("数据文件 (data/news_data.csv) 不存在或为空。请先运行 `get_news.py` 脚本来获取数据。")
else:
    sentiment_options = {"全部": "all", "利好": "bullish", "利空": "bearish", "中性": "neutral"}
    selected_sentiment = st.sidebar.selectbox("按情绪筛选", options=list(sentiment_options.keys()))
    all_keywords = ["沪金", "黄金期货", "COMEX黄金", "实物黄金", "黄金ETF", "美联储", "利率"]
    selected_keywords = st.sidebar.multiselect("按关键词筛选", options=all_keywords, default=all_keywords)
    start_date, end_date = None, None
    if not df['time'].isnull().all():
        actual_min_date = df['time'].min().date()
        actual_max_date = df['time'].max().date()
        picker_min_date = actual_min_date - timedelta(days=90)
        picker_max_date = datetime.now().date()
        st.sidebar.markdown("#### 选择日期范围")
        start_date = st.sidebar.date_input("开始日期", value=actual_min_date, min_value=picker_min_date, max_value=picker_max_date)
        end_date = st.sidebar.date_input("结束日期", value=actual_max_date, min_value=picker_min_date, max_value=picker_max_date)
    
    filtered_df = df.copy()
    if selected_keywords:
        keyword_mask = filtered_df.apply(lambda row: any(kw in str(row['title']) or kw in str(row['summary']) for kw in selected_keywords), axis=1)
        filtered_df = filtered_df[keyword_mask]
    if start_date and end_date:
        filtered_df = filtered_df[(filtered_df['time'].dt.date >= start_date) & (filtered_df['time'].dt.date <= end_date)]
    
    if 'sentiment_category' in filtered_df.columns and selected_sentiment != "全部":
        filtered_df = filtered_df[filtered_df['sentiment_category'] == selected_sentiment]

    st.subheader(f"共找到 {len(filtered_df)} 条相关新闻")
    
    filtered_df = filtered_df.sort_values(by='time', ascending=False)
    for _, row in filtered_df.iterrows():
        sentiment_class = row.get('sentiment_category', "中性").replace("利好", "bullish").replace("利空", "bearish").replace("中性", "neutral")
        sentiment_text = f"🐂 利好" if sentiment_class == "bullish" else (f"🐻 利空" if sentiment_class == "bearish" else f"😐 中性")

        st.markdown(f'''
        <div class="card">
            <div class="sentiment-badge {sentiment_class}">{sentiment_text}</div>
            <div class="news-title">{row["title"]}</div>
            <div class="news-summary">{textwrap.shorten(row["summary"], width=250, placeholder="...")}</div>
            <div class="news-meta">
                <span>{row['time'].strftime('%Y-%m-%d %H:%M')}</span>
                <a href="{row['url']}" target="_blank">阅读原文 &rarr;</a>
            </div>
        </div>
        ''', unsafe_allow_html=True)
    with st.expander("显示/隐藏原始数据表格"):
        st.dataframe(filtered_df, use_container_width=True)
