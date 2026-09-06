import re
from collections import Counter

import pandas as pd
import streamlit as st


def _title_keywords(title: str):
    stopwords = {
        "shorts", "youtube", "ゲーム", "動画", "配信", "切り抜き",
        "する", "した", "して", "です", "ます", "だ", "で", "の", "に",
        "が", "を", "と", "は", "も", "て", "た", "よ", "ね",
    }
    chunks = re.split(
        r"[\s　#、。,.!?！？:：/／|｜・…「」『』【】\[\]()（）]+",
        str(title),
    )
    out = []
    for word in chunks:
        word = word.strip("-_~〜")
        if len(word) < 2 or len(word) > 24:
            continue
        if word.lower() in stopwords or re.fullmatch(r"\d+", word):
            continue
        out.append(word)
    return out


def subscriber_potential(df: pd.DataFrame) -> pd.DataFrame:
    """公開データだけで算出する「登録者獲得ポテンシャル」。
    実際の動画別登録者増加数ではなく、再生・反応・鮮度などからの推定値。
    """
    x = df.copy()

    if x.empty:
        return x

    median_views = max(float(x["views"].median()), 1.0)
    median_engagement = max(float(x["engagement_score"].median()), 1.0)

    # 古すぎる動画の再生数だけでランキングが埋まらないよう、経過日数を軽く補正。
    age = x["days_since_publish"].clip(lower=1)
    velocity = x["views"] / age

    x["再生速度"] = velocity
    x["再生相対"] = (x["views"] / median_views).clip(lower=0)
    x["反応相対"] = (x["engagement_score"] / median_engagement).clip(lower=0)

    # 登録者獲得に向きやすい「見られる×反応される×最近」の複合スコア。
    x["登録者獲得ポテンシャル"] = (
        x["再生相対"].pow(0.45)
        * x["反応相対"].pow(0.35)
        * (1 + (x["like_rate"].clip(lower=0, upper=10) / 10) * 0.20)
        * (1 + (x["comment_rate"].clip(lower=0, upper=2) / 2) * 0.15)
    )

    recent = x["days_since_publish"] <= 90
    x.loc[recent, "登録者獲得ポテンシャル"] *= 1.10
    x["登録者獲得ポテンシャル"] = x["登録者獲得ポテンシャル"].round(2)

    return x


def make_subscriber_ideas(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    x = subscriber_potential(df)
    if x.empty:
        return pd.DataFrame(columns=["順位", "企画案", "狙い", "参考動画"])

    top = x.sort_values(
        ["登録者獲得ポテンシャル", "views"],
        ascending=False,
    ).head(20)

    keywords = Counter()
    for title in top["title"].astype(str):
        for word in _title_keywords(title):
            keywords[word] += 1

    candidates = []
    used = set()

    # 実績テーマを少し変えて再利用する企画。
    for _, row in top.iterrows():
        words = _title_keywords(row["title"])
        if not words:
            continue
        key = words[0]
        if key in used:
            continue
        used.add(key)

        candidates.append(
            {
                "企画案": f"「{key}」を軸にした反応型Shorts",
                "狙い": "実績テーマを維持しつつ、コメント・登録につながる形で再テスト",
                "参考動画": row["title"],
                "スコア": row["登録者獲得ポテンシャル"],
            }
        )

    # キーワードが取れない場合でも、上位動画から企画を出す。
    if len(candidates) < n:
        for _, row in top.iterrows():
            title = str(row["title"])
            if title in {c["参考動画"] for c in candidates}:
                continue
            candidates.append(
                {
                    "企画案": f"「{title}」の続編・別パターン",
                    "狙い": "既に実績のある動画をシリーズ化して再訪・登録を狙う",
                    "参考動画": title,
                    "スコア": row["登録者獲得ポテンシャル"],
                }
            )
            if len(candidates) >= n:
                break

    out = pd.DataFrame(candidates[:n])
    if out.empty:
        return pd.DataFrame(columns=["順位", "企画案", "狙い", "参考動画"])

    out.insert(0, "順位", range(1, len(out) + 1))
    return out[["順位", "企画案", "狙い", "参考動画"]]


def render_subscriber_growth(df: pd.DataFrame, subscribers: int = 0):
    st.header("👤 登録者増加AI")

    st.info(
        "目的を「再生数」から「登録者を増やすこと」に寄せた分析です。"
        "現在のYouTube Data API公開データでは動画別の実登録者増加数を取得できないため、"
        "下記は「登録者獲得ポテンシャル」の推定です。"
    )

    scored = subscriber_potential(df)

    if scored.empty:
        st.warning("動画データがありません。先に「YouTubeデータを取得・更新」を押してください。")
        return

    top = scored.sort_values(
        ["登録者獲得ポテンシャル", "views"],
        ascending=False,
    ).head(10).copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("現在の登録者", f"{int(subscribers):,}")
    c2.metric("登録者向け上位動画", f"{len(top):,}本")
    c3.metric("上位動画の中央値再生", f"{top['views'].median():,.0f}")

    st.subheader("🏆 登録者を増やす候補TOP10")
    view = top[[
        "title", "views", "like_rate", "comment_rate",
        "is_shorts", "登録者獲得ポテンシャル", "url"
    ]].copy()
    view["is_shorts"] = view["is_shorts"].map({True: "Shorts", False: "通常"})
    view = view.rename(columns={
        "title": "タイトル",
        "views": "再生数",
        "like_rate": "高評価率",
        "comment_rate": "コメント率",
        "is_shorts": "種別",
        "url": "YouTube",
    })

    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config={
            "YouTube": st.column_config.LinkColumn("YouTube"),
            "再生数": st.column_config.NumberColumn("再生数", format="%d"),
            "高評価率": st.column_config.NumberColumn("高評価率", format="%.2f%%"),
            "コメント率": st.column_config.NumberColumn("コメント率", format="%.3f%%"),
            "登録者獲得ポテンシャル": st.column_config.NumberColumn(
                "登録者獲得ポテンシャル", format="%.2f"
            ),
        },
    )

    st.subheader("📈 登録者を増やすための現在方針")
    shorts = scored[scored["is_shorts"]]
    long = scored[~scored["is_shorts"]]

    if not shorts.empty and not long.empty:
        shorts_score = shorts["登録者獲得ポテンシャル"].median()
        long_score = long["登録者獲得ポテンシャル"].median()

        if shorts_score >= long_score:
            st.success(
                "現状はShortsを入口にして、反応の良いテーマをシリーズ化する方針が有力です。"
                "ただし「再生だけを取りに行くShorts」ではなく、場末ノゾベを覚えてもらえるシリーズ型を優先します。"
            )
        else:
            st.success(
                "通常動画側の登録者獲得ポテンシャルが高めです。"
                "Shortsから通常動画へ誘導する導線を強化するのが有力です。"
            )

    st.subheader("💡 次に作る「登録者目的」10本")
    ideas = make_subscriber_ideas(df, 10)
    st.dataframe(ideas, use_container_width=True, hide_index=True)

    st.caption(
        "重要：実際の「動画ごとの登録者増加数」を使うには、将来的にYouTube Analytics APIの"
        "認証を追加する必要があります。この画面は現時点では公開データだけで動く推定版です。"
    )
