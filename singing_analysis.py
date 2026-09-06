import re

import pandas as pd
import streamlit as st


SINGING_KEYWORDS = [
    "歌枠", "歌", "歌ってみた", "歌唱", "カラオケ", "karaoke",
    "singing", "sing", "cover", "弾き語り", "アカペラ", "合唱",
    "歌リレー", "歌配信", "歌う", "歌います", "歌って", "歌える",
]


def _safe_contains(series, pattern):
    return series.astype(str).str.contains(pattern, case=False, regex=True, na=False)


def detect_singing(df: pd.DataFrame) -> pd.DataFrame:
    """タイトル中心に歌関連動画を推定する。公開APIだけで動く簡易分類。"""
    x = df.copy()
    if x.empty:
        x["is_singing"] = pd.Series(dtype=bool)
        x["singing_score"] = pd.Series(dtype=float)
        return x

    title = x["title"].fillna("").astype(str)
    score = pd.Series(0.0, index=x.index)

    for kw in SINGING_KEYWORDS:
        hit = title.str.contains(re.escape(kw), case=False, regex=True, na=False)
        score = score + hit.astype(float)

    # ライブ・配信系タイトルは歌関連の判定を少し強める。
    live = _safe_contains(title, r"ライブ|配信|stream|live")
    score = score + (live & (score > 0)).astype(float) * 0.5

    # 「歌」が単独で入っている場合はノイズを減らすため、短すぎる一致は別キーワードでも確認。
    x["is_singing"] = score >= 1
    x["singing_score"] = score.round(1)
    return x


def _median(df):
    return float(df["views"].median()) if not df.empty else 0.0


def _avg(df):
    return float(df["views"].mean()) if not df.empty else 0.0


def _lift(base, compare):
    if base <= 0:
        return 0.0
    return compare / base


def singing_summary(df: pd.DataFrame):
    x = detect_singing(df)
    singing = x[x["is_singing"]].copy()
    other = x[~x["is_singing"]].copy()

    singing_shorts = singing[singing["is_shorts"]]
    singing_long = singing[~singing["is_shorts"]]

    return {
        "df": x,
        "singing": singing,
        "other": other,
        "singing_median": _median(singing),
        "other_median": _median(other),
        "singing_avg": _avg(singing),
        "other_avg": _avg(other),
        "singing_shorts_median": _median(singing_shorts),
        "singing_long_median": _median(singing_long),
    }


def make_singing_ideas(singing: pd.DataFrame, n=10):
    if singing.empty:
        return pd.DataFrame(columns=["順位", "企画案", "狙い", "参考動画"])

    top = singing.sort_values(["views", "engagement_score"], ascending=False).head(20)
    templates = [
        "【歌枠】「{title}」系の選曲で30〜60分の歌枠",
        "【Shorts】歌枠の一番良かった1フレーズを切り抜き",
        "【Shorts】視聴者参加型「次に歌う曲を決めて」",
        "【歌枠】懐かしめの曲だけでセットリストを組む",
        "【歌枠】コメントで選曲するリクエスト歌枠",
        "【Shorts】歌枠の面白い瞬間・ハプニングを切り抜き",
        "【歌枠】テーマ縛りで歌う（アニソン・ゲーム曲など）",
        "【Shorts】歌う前→歌った後のギャップを1本にする",
        "【歌枠】深夜向けの落ち着いた選曲で配信",
        "【Shorts】歌枠から反応の良かった曲を別テイクで再テスト",
    ]

    rows = []
    for i in range(n):
        row = top.iloc[i % len(top)]
        rows.append({
            "順位": i + 1,
            "企画案": templates[i % len(templates)].format(title=str(row["title"])),
            "狙い": "過去の歌関連動画を入口に、Shorts→歌枠→登録の導線を作る",
            "参考動画": row["title"],
        })
    return pd.DataFrame(rows)


def render_singing_analysis(df: pd.DataFrame):
    st.header("🎤 歌枠分析AI")
    st.info(
        "タイトルから歌関連動画を推定し、歌枠・歌Shortsの再生実績を通常動画と比較します。"
        "分類は公開データだけを使った簡易判定なので、完全一致ではありません。"
    )

    result = singing_summary(df)
    x = result["df"]
    singing = result["singing"]

    if singing.empty:
        st.warning(
            "歌関連と判定できる動画がありませんでした。"
            "タイトルに「歌枠」「歌ってみた」「カラオケ」などを含む動画があるか確認してください。"
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("歌関連動画", f"{len(singing):,}本")
    c2.metric("歌関連・再生中央値", f"{result['singing_median']:,.0f}")
    c3.metric("その他・再生中央値", f"{result['other_median']:,.0f}")
    lift = _lift(result["other_median"], result["singing_median"])
    c4.metric("歌関連の相対値", f"{lift:.2f}倍")

    st.subheader("🔎 歌枠は伸びそう？")
    if result["singing_median"] >= result["other_median"] * 1.2 and result["other_median"] > 0:
        st.success(
            "データ上、歌関連動画の中央値が通常動画より強めです。"
            "歌枠を定期コンテンツとしてテストする価値が高いです。"
        )
    elif result["singing_median"] >= result["other_median"] * 0.8 and result["other_median"] > 0:
        st.info(
            "歌関連動画は大きく弱くありません。"
            "歌枠単体で勝負するより、歌Shortsを入口にして歌枠へ誘導する形でテストするのがおすすめです。"
        )
    else:
        st.warning(
            "現状の公開データでは歌関連動画の中央値が弱めです。"
            "いきなり歌枠を増やすより、まず歌Shortsを数本テストして反応を見る方が安全です。"
        )

    st.subheader("📊 歌関連の内訳")
    a, b, c = st.columns(3)
    a.metric("歌Shorts候補", f"{len(singing[singing['is_shorts']]):,}本")
    b.metric("歌・通常動画", f"{len(singing[~singing['is_shorts']]):,}本")
    btm = result["singing_shorts_median"]
    c.metric("歌Shorts中央値", f"{btm:,.0f}")

    st.subheader("🏆 歌関連動画TOP20")
    top = singing.sort_values(["views", "engagement_score"], ascending=False).head(20).copy()
    top["公開日"] = top["published_at"].dt.strftime("%Y-%m-%d")
    top["再生数"] = top["views"].map(lambda v: f"{int(v):,}")
    top["高評価率"] = top["like_rate"].map(lambda v: f"{v:.2f}%")
    top["種別"] = top["is_shorts"].map({True: "Shorts候補", False: "通常"})
    st.dataframe(
        top[["title", "公開日", "再生数", "高評価率", "種別", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "title": "タイトル",
            "url": st.column_config.LinkColumn("YouTube"),
        },
    )

    st.subheader("🧪 歌枠の勝ちパターン候補")
    st.caption("歌関連動画の中で、再生・反応が強いタイトル要素を確認できます。")
    keywords = []
    for title in singing["title"].fillna("").astype(str):
        for kw in SINGING_KEYWORDS:
            if re.search(re.escape(kw), title, flags=re.IGNORECASE):
                keywords.append(kw)
    if keywords:
        counts = pd.Series(keywords).value_counts().rename_axis("キーワード").reset_index(name="該当本数")
        counts["該当動画の再生中央値"] = [
            _median(singing[_safe_contains(singing["title"], re.escape(k))]) for k in counts["キーワード"]
        ]
        st.dataframe(counts, use_container_width=True, hide_index=True)

    st.subheader("💡 歌枠をやるなら、この10本")
    ideas = make_singing_ideas(singing, 10)
    st.dataframe(ideas, use_container_width=True, hide_index=True)

    st.subheader("🎯 登録者を増やすなら")
    st.markdown(
        "1. **歌Shorts**で新規視聴者に発見してもらう  \n"
        "2. 反応の良かった曲・形式を**歌枠**に展開  \n"
        "3. 歌枠から面白い場面を再び**Shorts化**  \n"
        "4. 同じシリーズ名・サムネ構成で繰り返して、場末ノゾベを覚えてもらう"
    )

    st.download_button(
        "⬇️ 歌関連動画だけCSV保存",
        data=singing.to_csv(index=False).encode("utf-8-sig"),
        file_name="basue_nozobe_singing_videos.csv",
        mime="text/csv",
        use_container_width=True,
    )
