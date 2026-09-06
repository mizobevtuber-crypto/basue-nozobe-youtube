import os
from datetime import datetime, timezone
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="場末ノゾベ YouTube分析", page_icon="📺", layout="wide")

API_KEY = st.secrets.get("YOUTUBE_API_KEY", os.getenv("YOUTUBE_API_KEY", ""))
CHANNEL_ID = st.secrets.get("CHANNEL_ID", os.getenv("CHANNEL_ID", ""))

BASE = "https://www.googleapis.com/youtube/v3"

@st.cache_data(ttl=600)
def yt_get(endpoint, params):
    p = dict(params)
    p["key"] = API_KEY
    r = requests.get(f"{BASE}/{endpoint}", params=p, timeout=30)
    r.raise_for_status()
    return r.json()

def get_channel():
    data = yt_get("channels", {
        "part": "snippet,contentDetails,statistics",
        "id": CHANNEL_ID
    })
    if not data.get("items"):
        raise ValueError("CHANNEL_ID が見つかりません。")
    return data["items"][0]

def sync_all_videos(channel):
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None

    while True:
        p = {"part":"contentDetails","playlistId":uploads,"maxResults":50}
        if token:
            p["pageToken"] = token
        page = yt_get("playlistItems", p)
        ids += [x["contentDetails"]["videoId"] for x in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break

    rows = []
    for i in range(0, len(ids), 50):
        data = yt_get("videos", {
            "part":"snippet,contentDetails,statistics",
            "id":",".join(ids[i:i+50])
        })
        for v in data.get("items", []):
            s, cd, st = v.get("snippet",{}), v.get("contentDetails",{}), v.get("statistics",{})
            rows.append({
                "video_id": v["id"],
                "title": s.get("title",""),
                "published_at": s.get("publishedAt",""),
                "duration": cd.get("duration",""),
                "views": int(st.get("viewCount",0)),
                "likes": int(st.get("likeCount",0)),
                "comments": int(st.get("commentCount",0)),
                "thumbnail": (s.get("thumbnails",{}).get("high",{}).get("url","")),
                "url": f"https://www.youtube.com/watch?v={v['id']}"
            })
    return pd.DataFrame(rows)

def iso_duration_seconds(x):
    import re
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", x or "")
    if not m:
        return 0
    h, mi, sec = [int(v or 0) for v in m.groups()]
    return h*3600 + mi*60 + sec

st.title("📺 場末ノゾベ YouTube分析")
st.caption("YouTube Data API v3 を使って公開動画データを取得します。")

if not API_KEY or not CHANNEL_ID:
    st.error("設定が必要です。Streamlit Cloudなら Settings → Secrets に YOUTUBE_API_KEY と CHANNEL_ID を設定してください。")
    st.code('YOUTUBE_API_KEY="あなたのAPIキー"\nCHANNEL_ID="チャンネルID"')
    st.stop()

try:
    channel = get_channel()
except Exception as e:
    st.error(f"チャンネル取得エラー: {e}")
    st.stop()

cs = channel.get("statistics", {})
col1, col2, col3, col4 = st.columns(4)
col1.metric("登録者数", f"{int(cs.get('subscriberCount',0)):,}")
col2.metric("総再生数", f"{int(cs.get('viewCount',0)):,}")
col3.metric("動画数", f"{int(cs.get('videoCount',0)):,}")
col4.metric("チャンネル", channel["snippet"].get("title",""))

if st.button("🔄 動画データを取得・更新", type="primary", use_container_width=True):
    with st.spinner("YouTubeから動画データを取得中…"):
        try:
            st.session_state["videos"] = sync_all_videos(channel)
            st.success(f"{len(st.session_state['videos'])}本を取得しました。")
        except Exception as e:
            st.error(f"取得に失敗しました: {e}")

df = st.session_state.get("videos")

if df is None:
    st.info("「動画データを取得・更新」を押してください。")
    st.stop()

df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
df["duration_sec"] = df["duration"].map(iso_duration_seconds)
df["like_rate"] = (df["likes"] / df["views"].replace(0, 1) * 100).round(2)
df["comment_rate"] = (df["comments"] / df["views"].replace(0, 1) * 100).round(3)

st.divider()
a,b,c,d = st.columns(4)
a.metric("平均再生数", f"{df.views.mean():,.0f}")
b.metric("中央値", f"{df.views.median():,.0f}")
c.metric("最高再生数", f"{df.views.max():,.0f}")
d.metric("平均高評価率", f"{df.like_rate.mean():.2f}%")

st.subheader("🔥 再生数ランキング")
top = df.sort_values("views", ascending=False).head(20).copy()
top["再生数"] = top["views"].map(lambda x:f"{x:,}")
top["高評価率"] = top["like_rate"].map(lambda x:f"{x:.2f}%")
st.dataframe(top[["title","published_at","再生数","高評価率","comments","url"]], use_container_width=True, hide_index=True)

st.subheader("📈 再生数の分布")
chart = df.sort_values("published_at")[["published_at","views"]].set_index("published_at")
st.line_chart(chart)

st.subheader("🔎 動画検索")
q = st.text_input("タイトルで検索")
view = df if not q else df[df["title"].str.contains(q, case=False, na=False)]
st.dataframe(
    view.sort_values("published_at", ascending=False)[
        ["title","published_at","views","likes","comments","like_rate","url"]
    ],
    use_container_width=True, hide_index=True
)

st.download_button(
    "⬇️ CSVを保存",
    data=df.to_csv(index=False).encode("utf-8-sig"),
    file_name="basue_nozobe_youtube.csv",
    mime="text/csv",
    use_container_width=True
)

st.caption(f"最終取得: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
