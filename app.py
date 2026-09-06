import os
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

from subscriber_growth_ai import render_subscriber_growth

st.set_page_config(
    page_title="場末ノゾベ YouTube戦略AI",
    page_icon="🧠",
    layout="wide",
)

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
    data = yt_get(
        "channels",
        {
            "part": "snippet,contentDetails,statistics",
            "id": CHANNEL_ID,
        },
    )
    if not data.get("items"):
        raise ValueError("CHANNEL_ID が見つかりません。")
    return data["items"][0]


def sync_all_videos(channel):
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None

    while True:
        params = {
            "part": "contentDetails",
            "playlistId": uploads,
            "maxResults": 50,
        }
        if token:
            params["pageToken"] = token

        page = yt_get("playlistItems", params)
        ids += [
            x["contentDetails"]["videoId"]
            for x in page.get("items", [])
            if x.get("contentDetails", {}).get("videoId")
        ]

        token = page.get("nextPageToken")
        if not token:
            break

    rows = []
    for i in range(0, len(ids), 50):
        data = yt_get(
            "videos",
            {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(ids[i : i + 50]),
            },
        )

        for v in data.get("items", []):
            s = v.get("snippet", {})
            cd = v.get("contentDetails", {})
            stats = v.get("statistics", {})

            rows.append(
                {
                    "video_id": v["id"],
                    "title": s.get("title", ""),
                    "published_at": s.get("publishedAt", ""),
                    "duration": cd.get("duration", ""),
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                    "thumbnail": s.get("thumbnails", {})
                    .get("high", {})
                    .get("url", ""),
                    "url": f"https://www.youtube.com/watch?v={v['id']}",
                }
            )

    return pd.DataFrame(rows)


def iso_duration_seconds(value):
    m = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or ""
    )
    if not m:
        return 0

    h, mi, sec = [int(v or 0) for v in m.groups()]
    return h * 3600 + mi * 60 + sec


def prepare_dataframe(df):
    df = df.copy()

    df["published_at"] = pd.to_datetime(
        df["published_at"], errors="coerce", utc=True
    )
    df["duration_sec"] = df["duration"].map(iso_duration_seconds)

    safe_views = df["views"].replace(0, 1)
    df["like_rate"] = (df["likes"] / safe_views * 100).round(2)
    df["comment_rate"] = (df["comments"] / safe_views * 100).round(3)

    # YouTube Shortsの判定はAPIだけでは完全には取得できないため、
    # #shortsを含むタイトル、または3分以内の動画を「Shorts候補」とする。
    df["is_shorts"] = (
        df["title"].str.contains(r"#shorts\b", case=False, regex=True, na=False)
        | (df["duration_sec"] <= 180)
    )

    df["days_since_publish"] = (
        pd.Timestamp.now(tz="UTC") - df["published_at"]
    ).dt.total_seconds() / 86400

    # 古い動画と新しい動画を単純比較しすぎないための簡易スコア。
    # 「再生数だけ」ではなく、高評価・コメントも加味する。
    df["engagement_score"] = (
        df["views"]
        * (
            1
            + df["like_rate"].clip(lower=0, upper=20) / 100
            + df["comment_rate"].clip(lower=0, upper=5) / 100
        )
    )

    return df


def extract_keywords(titles):
    """
    日本語形態素解析に依存しない簡易キーワード抽出。
    ハッシュタグ、記号区切りの語、英数字語を中心に抽出する。
    """
    counter = Counter()

    stopwords = {
        "shorts",
        "youtube",
        "ゲーム",
        "動画",
        "配信",
        "切り抜き",
        "する",
        "した",
        "して",
        "です",
        "ます",
        "だ",
        "で",
        "の",
        "に",
        "が",
        "を",
        "と",
        "は",
        "も",
        "て",
        "た",
        "よ",
        "ね",
        "から",
        "これ",
        "それ",
        "ここ",
    }

    for title in titles.dropna().astype(str):
        hashtags = re.findall(r"#([^\s#]+)", title)
        for word in hashtags:
            word = word.strip("「」『』【】[]()（）.,!?！？")
            if len(word) >= 2 and word.lower() not in stopwords:
                counter[word] += 2

        chunks = re.split(
            r"[\s　#、。,.!?！？:：/／|｜・…「」『』【】\[\]()（）]+",
            title,
        )

        for word in chunks:
            word = word.strip("-_~〜")
            if len(word) < 2 or len(word) > 24:
                continue
            if word.lower() in stopwords:
                continue
            if re.fullmatch(r"\d+", word):
                continue
            counter[word] += 1

    return counter


def keyword_performance(df):
    rows = []

    for keyword, count in extract_keywords(df["title"]).most_common(40):
        mask = df["title"].str.contains(
            re.escape(keyword), case=False, regex=True, na=False
        )
        subset = df[mask]

        if len(subset) < 2:
            continue

        rows.append(
            {
                "キーワード": keyword,
                "本数": len(subset),
                "平均再生": int(subset["views"].mean()),
                "中央値": int(subset["views"].median()),
                "最高再生": int(subset["views"].max()),
                "平均高評価率": round(subset["like_rate"].mean(), 2),
            }
        )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["中央値", "本数"], ascending=[False, False]
    )


def format_duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds // 3600}時間{(seconds % 3600) // 60:02d}分"


def build_strategy(df):
    median_views = max(float(df["views"].median()), 1)
    avg_views = float(df["views"].mean())

    shorts = df[df["is_shorts"]]
    long_videos = df[~df["is_shorts"]]

    def median_or_zero(x):
        return float(x["views"].median()) if len(x) else 0

    shorts_median = median_or_zero(shorts)
    long_median = median_or_zero(long_videos)

    top = df.sort_values("views", ascending=False).head(20)
    top_shorts = top[top["is_shorts"]]

    recent_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=90)
    recent = df[df["published_at"] >= recent_cutoff]

    recent_median = median_or_zero(recent)

    keyword_df = keyword_performance(df)
    strong_keywords = keyword_df.head(5) if not keyword_df.empty else pd.DataFrame()

    recommendations = []

    if shorts_median > 0 and long_median > 0:
        if shorts_median >= long_median * 1.5:
            recommendations.append(
                "現状はShorts寄りの再生獲得が有利。まずShortsを主軸にして、反応の良いテーマを通常動画へ展開する。"
            )
        elif long_median >= shorts_median * 1.5:
            recommendations.append(
                "通常動画の中央値がShortsより強い。Shortsだけに寄せず、通常動画を軸にしてShortsを入口として使う。"
            )
        else:
            recommendations.append(
                "Shortsと通常動画の差が大きくない。両方を残し、テーマ別の勝ちパターンを探す段階。"
            )

    if recent_median > 0:
        ratio = recent_median / median_views
        if ratio >= 1.2:
            recommendations.append(
                f"直近90日の再生中央値は全期間中央値の約{ratio:.1f}倍。最近の方向性は維持・強化する価値が高い。"
            )
        elif ratio <= 0.7:
            recommendations.append(
                "直近90日の再生中央値が全期間中央値を下回っている。直近のタイトル・テーマ・冒頭構成を重点的に見直す。"
            )
        else:
            recommendations.append(
                "直近90日の再生中央値は全期間と大きく離れていない。次の改善ではタイトルと冒頭の差分検証が重要。"
            )

    if strong_keywords is not None and not strong_keywords.empty:
        kw = "、".join(str(x) for x in strong_keywords["キーワード"].head(3))
        recommendations.append(
            f"過去動画では「{kw}」など、実績のあるキーワードを含むテーマを優先的に派生させる。"
        )

    top_share = (
        top["views"].sum() / max(df["views"].sum(), 1) * 100
        if len(top)
        else 0
    )

    return {
        "median_views": median_views,
        "avg_views": avg_views,
        "shorts_median": shorts_median,
        "long_median": long_median,
        "recent_median": recent_median,
        "top_share": top_share,
        "top_shorts": top_shorts,
        "keyword_df": keyword_df,
        "recommendations": recommendations,
    }


def make_next_ideas(df, n=30):
    """
    過去の実績から「企画の型」を生成する。
    LLMではなくデータ駆動のため、APIキー追加なしで動作する。
    """
    top = df.sort_values("views", ascending=False).head(30)
    keyword_df = keyword_performance(df)

    keywords = (
        keyword_df["キーワード"].head(12).tolist()
        if not keyword_df.empty
        else []
    )

    ideas = []

    templates = [
        "【再現】{kw}で一番伸びたパターンをもう一度試す",
        "{kw}で「やってみた」系Shorts",
        "{kw}で「まさかの結果」系Shorts",
        "{kw}の意外なポイントを30〜60秒で紹介",
        "{kw}について視聴者に質問するShorts",
        "{kw}の過去動画をセルフ切り抜きして再構成",
    ]

    if not keywords:
        keywords = ["過去の人気テーマ"]

    for i in range(n):
        kw = keywords[i % len(keywords)]
        template = templates[i % len(templates)]
        ideas.append(
            {
                "順位": i + 1,
                "企画案": template.format(kw=kw),
                "狙い": "過去データで実績のあるテーマ・タイトル型から派生",
                "参考動画": top.iloc[i % len(top)]["title"] if len(top) else "",
            }
        )

    return pd.DataFrame(ideas)


st.title("🧠 場末ノゾベ YouTube戦略AI")
st.caption(
    "YouTube Data API v3の公開データを分析し、過去動画から次の投稿戦略を作ります。"
)

if not API_KEY or not CHANNEL_ID:
    st.error(
        "設定が必要です。Streamlit Cloudの Settings → Secrets に "
        "YOUTUBE_API_KEY と CHANNEL_ID を設定してください。"
    )
    st.code('YOUTUBE_API_KEY="あなたのAPIキー"\nCHANNEL_ID="チャンネルID"')
    st.stop()

try:
    channel = get_channel()
except Exception as e:
    st.error(f"チャンネル取得エラー: {e}")
    st.stop()

cs = channel.get("statistics", {})

col1, col2, col3, col4 = st.columns(4)
col1.metric("登録者数", f"{int(cs.get('subscriberCount', 0)):,}")
col2.metric("総再生数", f"{int(cs.get('viewCount', 0)):,}")
col3.metric("動画数", f"{int(cs.get('videoCount', 0)):,}")
col4.metric("チャンネル", channel["snippet"].get("title", ""))

if st.button(
    "🔄 YouTubeデータを取得・更新",
    type="primary",
    use_container_width=True,
):
    with st.spinner("YouTubeから動画データを取得中…"):
        try:
            st.session_state["videos"] = sync_all_videos(channel)
            st.success(
                f"{len(st.session_state['videos'])}本を取得しました。"
            )
        except Exception as e:
            st.error(f"取得に失敗しました: {e}")

df = st.session_state.get("videos")

if df is None:
    st.info("「YouTubeデータを取得・更新」を押してください。")
    st.stop()

df = prepare_dataframe(df)

strategy = build_strategy(df)

st.divider()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "🏠 ダッシュボード",
        "🧠 戦略AI",
        "🔥 伸びた動画",
        "✂️ Shorts分析",
        "💡 次の30本",
        "👤 登録者増加AI",
    ]
)

with tab1:
    a, b, c, d = st.columns(4)
    a.metric("平均再生数", f"{df['views'].mean():,.0f}")
    b.metric("再生中央値", f"{df['views'].median():,.0f}")
    c.metric("最高再生数", f"{df['views'].max():,.0f}")
    d.metric("平均高評価率", f"{df['like_rate'].mean():.2f}%")

    st.subheader("📈 再生数の推移")
    chart = (
        df.sort_values("published_at")[["published_at", "views"]]
        .set_index("published_at")
    )
    st.line_chart(chart)

    st.subheader("🔎 動画検索")
    q = st.text_input("タイトルで検索", key="dashboard_search")
    view = (
        df
        if not q
        else df[df["title"].str.contains(q, case=False, na=False)]
    )

    display = view.sort_values("published_at", ascending=False).copy()
    display["公開日"] = display["published_at"].dt.strftime("%Y-%m-%d")
    display["再生数"] = display["views"].map(lambda x: f"{x:,}")
    display["高評価率"] = display["like_rate"].map(lambda x: f"{x:.2f}%")

    st.dataframe(
        display[
            [
                "title",
                "公開日",
                "再生数",
                "高評価率",
                "comments",
                "url",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "title": "タイトル",
            "comments": "コメント",
            "url": st.column_config.LinkColumn("YouTube"),
        },
    )

with tab2:
    st.header("🧠 場末ノゾベ専用・戦略診断")

    st.info(
        "これは単なる再生数ランキングではなく、過去動画の中央値・"
        "Shorts/通常動画・直近90日・タイトルの実績を組み合わせた"
        "データ駆動の戦略エンジンです。"
    )

    st.subheader("現在の診断")

    for i, recommendation in enumerate(strategy["recommendations"], 1):
        st.markdown(f"**{i}. {recommendation}**")

    st.divider()

    x, y, z = st.columns(3)
    x.metric(
        "Shorts再生中央値",
        f"{strategy['shorts_median']:,.0f}",
    )
    y.metric(
        "通常動画再生中央値",
        f"{strategy['long_median']:,.0f}",
    )
    z.metric(
        "直近90日再生中央値",
        f"{strategy['recent_median']:,.0f}",
    )

    st.subheader("🔑 伸びやすいテーマ候補")

    if not strategy["keyword_df"].empty:
        kw = strategy["keyword_df"].head(15).copy()
        kw["平均再生"] = kw["平均再生"].map(lambda x: f"{x:,}")
        kw["中央値"] = kw["中央値"].map(lambda x: f"{x:,}")
        kw["最高再生"] = kw["最高再生"].map(lambda x: f"{x:,}")
        kw["平均高評価率"] = kw["平均高評価率"].map(
            lambda x: f"{x:.2f}%"
        )
        st.dataframe(kw, use_container_width=True, hide_index=True)
    else:
        st.warning("タイトルから十分なキーワード候補を抽出できませんでした。")

    st.subheader("🎯 戦略の使い方")
    st.markdown(
        """
        **おすすめの運用方法**

        1. 過去の上位動画から「テーマ」を選ぶ
        2. 同じテーマでタイトル・冒頭・オチを変えて複数本テスト
        3. 伸びたものを通常動画や次のShortsへ展開
        4. 「次の30本」から企画候補を作る
        """
    )

with tab3:
    st.header("🔥 伸びた動画TOP20")

    top = df.sort_values(
        ["views", "engagement_score"], ascending=False
    ).head(20).copy()

    top["公開日"] = top["published_at"].dt.strftime("%Y-%m-%d")
    top["再生数"] = top["views"].map(lambda x: f"{x:,}")
    top["高評価率"] = top["like_rate"].map(lambda x: f"{x:.2f}%")
    top["コメント率"] = top["comment_rate"].map(lambda x: f"{x:.3f}%")
    top["種別"] = top["is_shorts"].map(
        {True: "Shorts候補", False: "通常動画"}
    )

    st.dataframe(
        top[
            [
                "title",
                "公開日",
                "再生数",
                "高評価率",
                "コメント率",
                "種別",
                "url",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "title": "タイトル",
            "url": st.column_config.LinkColumn("YouTube"),
        },
    )

    st.subheader("🏆 上位動画に共通するタイトル要素")

    top_keywords = keyword_performance(top)
    if not top_keywords.empty:
        st.dataframe(
            top_keywords.head(15),
            use_container_width=True,
            hide_index=True,
        )

with tab4:
    st.header("✂️ Shorts分析")

    shorts = df[df["is_shorts"]].copy()

    if shorts.empty:
        st.warning("Shorts候補が見つかりませんでした。")
    else:
        s1, s2, s3 = st.columns(3)
        s1.metric("Shorts候補", f"{len(shorts):,}本")
        s2.metric("再生中央値", f"{shorts['views'].median():,.0f}")
        s3.metric("最高再生", f"{shorts['views'].max():,.0f}")

        st.subheader("🔥 Shorts TOP20")
        shorts_top = shorts.sort_values("views", ascending=False).head(20)
        shorts_top = shorts_top.copy()
        shorts_top["公開日"] = shorts_top["published_at"].dt.strftime(
            "%Y-%m-%d"
        )
        shorts_top["再生数"] = shorts_top["views"].map(lambda x: f"{x:,}")

        st.dataframe(
            shorts_top[
                ["title", "公開日", "再生数", "like_rate", "url"]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": "タイトル",
                "like_rate": st.column_config.NumberColumn(
                    "高評価率", format="%.2f%%"
                ),
                "url": st.column_config.LinkColumn("YouTube"),
            },
        )

        st.subheader("🧬 Shortsの勝ちパターン候補")
        shorts_keywords = keyword_performance(shorts)

        if not shorts_keywords.empty:
            st.dataframe(
                shorts_keywords.head(20),
                use_container_width=True,
                hide_index=True,
            )

        st.caption(
            "Shorts判定は #shorts または動画時間3分以内を基準にした簡易判定です。"
            "YouTube上の正式なShorts判定とは一致しない場合があります。"
        )

with tab5:
    st.header("💡 次に作る30本")

    st.info(
        "過去の動画で実績があるタイトル要素・テーマから、"
        "次の投稿候補を自動生成しています。"
    )

    ideas = make_next_ideas(df, 30)

    st.dataframe(
        ideas,
        use_container_width=True,
        hide_index=True,
        column_config={
            "順位": st.column_config.NumberColumn("順位", width="small"),
            "企画案": "企画案",
            "狙い": "狙い",
            "参考動画": "参考動画",
        },
    )

    st.download_button(
        "⬇️ 30本の企画案をCSV保存",
        data=ideas.to_csv(index=False).encode("utf-8-sig"),
        file_name="basue_nozobe_next_30_ideas.csv",
        mime="text/csv",
        use_container_width=True,
    )

with tab6:
    render_subscriber_growth(df)

st.divider()

st.download_button(
    "⬇️ 全動画データをCSV保存",
    data=df.to_csv(index=False).encode("utf-8-sig"),
    file_name="basue_nozobe_youtube.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption(
    f"最終取得: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
)
