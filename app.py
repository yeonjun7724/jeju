import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from folium.features import DivIcon
from shapely.geometry import Point
import osmnx as ox
import requests
from streamlit_folium import st_folium
import openai
import math
import os
from urllib.parse import quote
import io

# ✅ 페이지 설정
st.set_page_config(
    page_title="제주온 - 제주도 맞춤형 AI기반 스마트 관광 가이드",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ✅ 환경변수
MAPBOX_TOKEN = "pk.eyJ1Ijoia2lteWVvbmp1biIsImEiOiJjbWwwcWVyOG8wZGZpM2RxeWJ0eW9rM3dmIn0.b2idyXvhTgzd4mHQT7Nr8A"
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ✅ 데이터 로드
@st.cache_data
def load_data():
    try:
        tour = pd.read_csv("dataset/관광업_좌표추가.csv", encoding="utf-8").rename(columns={"X": "lon", "Y": "lat"})
        tour["type"] = "관광업"

        cafe = pd.read_csv("dataset/음식점_카페_좌표추가.csv", encoding="utf-8").rename(columns={"X": "lon", "Y": "lat"})
        cafe["type"] = "음식점/카페"

        # ✅ 자연경관 데이터 추가 (접근성 컬럼 포함)
        natural = pd.read_csv("dataset/자연경관_좌표추가.csv", encoding="cp949").rename(columns={"X": "lon", "Y": "lat"})
        natural = natural.rename(columns={"X": "lon", "Y": "lat"})
        natural["type"] = "자연경관"

        # 필요하면 샘플링 (데이터가 많을 때만)
        if len(tour) > 100:
            tour = tour.sample(n=100, random_state=42)
        if len(cafe) > 100:
            cafe = cafe.sample(n=100, random_state=42)

        data = pd.concat([tour, cafe, natural], ignore_index=True)
        data = data.drop_duplicates(subset=["사업장명", "lon", "lat"])

        geometry = [Point(xy) for xy in zip(data["lon"], data["lat"])]
        gdf = gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326")

        boundary = ox.geocode_to_gdf("Jeju Island, South Korea")
        return gdf, boundary, data
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None, None

gdf, boundary, data = load_data()
data_loaded = gdf is not None
if not data_loaded:
    st.warning("⚠️ 관광 데이터 로드에 실패했어요. (지도/경로 기능은 숨기고, AI 추천은 사용 가능합니다.)")

# ✅ 카페 포맷 함수
def format_cafes(cafes_df):
    try:
        cafes_df = cafes_df.drop_duplicates(subset=['c_name', 'c_value', 'c_review'])
        if len(cafes_df) == 0:
            return ("현재 이 관광지 주변에 등록된 카페 정보는 없어요. \n"
                    "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니, \n"
                    "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊")
        elif len(cafes_df) == 1:
            row = cafes_df.iloc[0]
            if all(x not in str(row["c_review"]) for x in ["없음", "없읍"]):
                return f" **{row['c_name']}** (⭐ {row['c_value']}) \n\"{row['c_review']}\""
            else:
                return f"**{row['c_name']}** (⭐ {row['c_value']})"
        else:
            grouped = cafes_df.groupby(['c_name', 'c_value'])
            lines = ["**주변의 평점 높은 카페들은 여기 있어요!** 🌼\n"]
            for (name, value), group in grouped:
                reviews = group['c_review'].dropna().unique()
                reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                top_reviews = reviews[:3]
                if top_reviews:
                    review_text = "\n".join([f"\"{r}\"" for r in top_reviews])
                    lines.append(f"- **{name}** (⭐ {value}) \n{review_text}")
                else:
                    lines.append(f"- **{name}** (⭐ {value})")
            return "\n\n".join(lines)
    except Exception as e:
        return f"카페 정보 처리 중 오류가 발생했습니다: {str(e)}"

# ✅ Session 초기화
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 제주 문화관광 전문 가이드입니다."}],
    "auto_gpt_input": ""
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ✅ 스타일 (CSS)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
.main > div { padding-top: 1.2rem; padding-bottom: 0.5rem; }
header[data-testid="stHeader"] { display: none; }
.stApp { background: #f8f9fa; }
.header-container { display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:2rem; padding:1rem 0; }
.logo-image { width:50px; height:50px; object-fit:contain; }
.main-title { font-size:2.8rem; font-weight:700; color:#202124; letter-spacing:-1px; margin:0; }
.title-underline { width:100%; height:3px; background:linear-gradient(90deg,#4285f4,#34a853); margin:0 auto 2rem auto; border-radius:2px; }
.section-header { font-size:1.3rem; font-weight:700; color:#1f2937; margin-bottom:20px; display:flex; align-items:center; gap:8px; padding-bottom:12px; border-bottom:2px solid #f3f4f6; }
.stButton > button { background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border:none; border-radius:10px; padding:12px 20px; font-size:0.9rem; font-weight:600; width:100%; height:48px; transition:all .3s; box-shadow:0 4px 8px rgba(102,126,234,.3); }
.stButton > button:hover { transform:translateY(-2px); box-shadow:0 6px 16px rgba(102,126,234,.4); }
.visit-order-item { display:flex; align-items:center; padding:12px 16px; background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border-radius:12px; margin-bottom:8px; font-size:.95rem; font-weight:500; transition:.2s; box-shadow:0 2px 4px rgba(102,126,234,.3); }
.visit-order-item:hover { transform:translateX(4px); box-shadow:0 4px 8px rgba(102,126,234,.4); }
.visit-number { background:rgba(255,255,255,.9); color:#667eea; width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:700; margin-right:12px; flex-shrink:0; }
.stMetric { background:linear-gradient(135deg,#a8edea 0%,#fed6e3 100%); border:none; border-radius:12px; padding:16px 10px; text-align:center; transition:.2s; box-shadow:0 2px 4px rgba(168,237,234,.3); }
.stMetric:hover { transform:translateY(-2px); box-shadow:0 4px 8px rgba(168,237,234,.4); }
.empty-state { text-align:center; padding:40px 20px; color:#9ca3af; font-style:italic; font-size:.95rem; background:linear-gradient(135deg,#ffecd2 0%,#fcb69f 100%); border-radius:12px; margin:16px 0; }

/* 지도/iframe 여백 제거 */
div.element-container:has(#main_map),
div[data-testid="stElement"]:has(#main_map),
div[data-testid="stComponent"]:has(#main_map) { margin: 0 !important; padding: 0 !important; }
div[data-testid="stIFrame"]:has(> iframe),
div[data-testid="stIFrame"] > iframe { margin: 0 !important; padding: 0 !important; border: none !important; }
#main_map .folium-map, #main_map .leaflet-container { width: 100% !important; height: 100% !important; margin: 0 !important; padding: 0 !important; }

.block-container { padding-top:1rem; padding-bottom:1rem; max-width:1400px; }
.stSuccess { background:linear-gradient(135deg,#d4edda 0%,#c3e6cb 100%); border:1px solid #b8dacd; border-radius:8px; color:#155724; }
.stWarning { background:linear-gradient(135deg,#fff3cd 0%,#ffeaa7 100%); border:1px solid #f8d7da; border-radius:8px; color:#856404; }
.stError { background:linear-gradient(135deg,#f8d7da 0%,#f5c6cb 100%); border:1px solid #f1b0b7; border-radius:8px; color:#721c24; }
</style>
""", unsafe_allow_html=True)

# ✅ 헤더
st.markdown('''
<div class="header-container">
    <img src="https://raw.githubusercontent.com/JeongWon4034/jeju/main/logo_jeju.png" alt='제주온 로고' style="width:125px; height:125px;">
    <div class="main-title">제주온 - 제주도 맞춤형 AI기반 스마트 관광 가이드</div>
</div>
<div class="title-underline"></div>
''', unsafe_allow_html=True)

# ✅ 여행 성향 선택
with st.container():
    st.markdown("### ✈️ 여행 성향 선택하기")
    st.write("원하는 여행 분위기나 목적을 선택하세요. AI가 이에 맞는 장소를 추천합니다.")
    travel_style = st.multiselect(
        "여행 키워드 선택 (최대 3개)",
        ["힐링","감성","자연","체험","커플","가족","액티비티","사진명소","카페투어","맛집탐방"],
        default=["힐링"]
    )
    if travel_style:
        st.success(f"선택한 여행 성향: {', '.join(travel_style)}")
    else:
        st.info("여행 성향을 하나 이상 선택해주세요.")
    show_recommend = st.button("🔍 AI 추천 보기", key="ai_recommend_button")

    if show_recommend:
        if not travel_style:
            st.warning("먼저 여행 성향을 선택해주세요!")
        else:
            try:
                base = "https://raw.githubusercontent.com/JeongWon4034/jeju/main/"
                fname = "비짓제주_이름기반_감성분석결과.csv"
                url = base + quote(fname)

                @st.cache_data
                def load_ai_recommendations(url_):
                    r = requests.get(url_, timeout=15)
                    r.raise_for_status()
                    r.encoding = "utf-8"
                    return pd.read_csv(io.StringIO(r.text))

                rec_df = load_ai_recommendations(url)
                st.success(f"선택한 성향({', '.join(travel_style)})에 맞는 추천지를 추렸어요 💫")

                # 성향 필터
                pattern = "|".join(travel_style)
                filtered = rec_df[rec_df["최고추천성향"].astype(str).str.contains(pattern, na=False)]

                if filtered.empty:
                    st.error("해당 성향에 맞는 추천 결과가 없습니다 😢")
                else:
                    # 장소명 컬럼 결정
                    place_col = "관광지명" if "관광지명" in filtered.columns else filtered.columns[0]

                    # 점수 내림차순 → 장소명 중복 제거 → 최대 3개
                    filtered = (
                        filtered.sort_values(by="최고추천점수", ascending=False)
                                .drop_duplicates(subset=[place_col], keep="first")
                                .head(3)
                    )

                    # 카드 출력
                    for i, row in enumerate(filtered.to_dict("records"), 1):
                        title = row.get(place_col, "추천지")
                        style = row.get("최고추천성향", "")
                        score = row.get("최고추천점수", float("nan"))
                        cnt   = int(row.get("Cnt", 0)) if not pd.isna(row.get("Cnt", None)) else 0
                        link  = row.get("URL", "#")

                        st.markdown(f"""
                        <div style='background:linear-gradient(135deg,#fdfbfb 0%,#ebedee 100%);
                                    padding:16px;border-radius:12px;margin-bottom:12px;
                                    box-shadow:0 2px 5px rgba(0,0,0,0.05)'>
                            <h4 style='margin-bottom:4px'>🌟 {i}. {title}</h4>
                            <p style='margin:2px 0'>🧭 주요 성향: <b>{style}</b></p>
                            <p style='margin:2px 0'>💫 추천점수: <b>{score:.3f}</b></p>
                            <p style='margin:2px 0'>🔥 인기도(Cnt): {cnt}</p>
                            <a href='{link}' target='_blank'>🔗 자세히 보기</a>
                        </div>
                        """, unsafe_allow_html=True)
            except Exception as e:
                st.error("❌ 추천 데이터를 불러오는 중 오류가 발생했어요.")
                st.code(repr(e))

# ✅ 메인 레이아웃
if data_loaded:
    col1, col2, col3 = st.columns([1.5, 1.2, 3], gap="large")
else:
    st.info("📌 데이터가 준비되면 경로 추천/지도가 활성화됩니다.")

# ✅ 경로/방문 순서/지도
if data_loaded:
    # 좌측: 경로 설정
    with col1:
        st.markdown('<div class="section-header">🚗 추천경로 설정</div>', unsafe_allow_html=True)
        st.markdown("**이동 모드**")
        mode = st.radio("", ["운전자", "도보"], horizontal=True, key="mode_key", label_visibility="collapsed")
        st.markdown("**출발지**")
        start = st.selectbox("", gdf["사업장명"].dropna().unique(), key="start_key", label_visibility="collapsed")
        st.markdown("**경유지**")
        wps = st.multiselect("", [n for n in gdf["사업장명"].dropna().unique() if n != st.session_state.get("start_key", "")], key="wps_key", label_visibility="collapsed")
        c1, c2 = st.columns(2, gap="small")
        with c1:
            create_clicked = st.button("경로 생성")
        with c2:
            clear_clicked = st.button("초기화")

    # 초기화
    if clear_clicked:
        try:
            for k in ["segments", "order"]:
                st.session_state[k] = []
            for k in ["duration", "distance"]:
                st.session_state[k] = 0.0
            st.session_state["auto_gpt_input"] = ""
            for widget_key in ["mode_key", "start_key", "wps_key"]:
                if widget_key in st.session_state:
                    del st.session_state[widget_key]
            st.success("✅ 초기화가 완료되었습니다.")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 초기화 중 오류: {str(e)}")

    # 중간: 방문 순서 + 메트릭
    with col2:
        st.markdown('<div class="section-header">📍 여행 방문 순서</div>', unsafe_allow_html=True)
        current_order = st.session_state.get("order", [])
        if current_order:
            for i, name in enumerate(current_order, 1):
                st.markdown(f'''
                <div class="visit-order-item">
                    <div class="visit-number">{i}</div>
                    <div>{name}</div>
                </div>
                ''', unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty-state">경로 생성 후 표시됩니다<br>🗺️</div>', unsafe_allow_html=True)
        st.markdown("---")
        st.metric("⏱️ 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
        st.metric("📏 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

    # 우측: 지도
    with col3:
        st.markdown('<div class="section-header">🗺️ 추천경로 지도시각화</div>', unsafe_allow_html=True)
        try:
            ctr = boundary.geometry.centroid
            clat, clon = float(ctr.y.mean()), float(ctr.x.mean())
            if math.isnan(clat) or math.isnan(clon):
                clat, clon = 33.38, 126.53
        except Exception as e:
            st.warning(f"중심점 계산 오류: {str(e)}")
            clat, clon = 36.64, 127.48

        @st.cache_data
        def load_graph(lat, lon):
            try:
                return ox.graph_from_point((lat, lon), dist=3000, network_type="all")
            except Exception as e:
                st.warning(f"도로 네트워크 로드 실패: {str(e)}")
                try:
                    return ox.graph_from_point((36.64, 127.48), dist=3000, network_type="all")
                except:
                    return None

        G = load_graph(clat, clon)
        edges = None
        if G is not None:
            try:
                edges = ox.graph_to_gdfs(G, nodes=False)
            except Exception as e:
                st.warning(f"엣지 변환 실패: {str(e)}")

        stops = [start] + wps
        snapped = []

        try:
            for nm in stops:
                matching_rows = gdf[gdf["사업장명"] == nm]
                if matching_rows.empty:
                    st.warning(f"⚠️ '{nm}' 정보를 찾을 수 없습니다.")
                    continue
                r = matching_rows.iloc[0]
                if pd.isna(r.lon) or pd.isna(r.lat):
                    st.warning(f"⚠️ '{nm}'의 좌표 정보가 없습니다.")
                    continue
                pt = Point(r.lon, r.lat)
                if edges is None or edges.empty:
                    snapped.append((r.lon, r.lat))
                    continue
                edges["d"] = edges.geometry.distance(pt)
                if edges["d"].empty:
                    snapped.append((r.lon, r.lat))
                    continue
                ln = edges.loc[edges["d"].idxmin()]
                sp = ln.geometry.interpolate(ln.geometry.project(pt))
                snapped.append((sp.x, sp.y))
        except Exception as e:
            st.error(f"❌ 지점 처리 중 오류: {str(e)}")
            snapped = []
            for nm in stops:
                try:
                    r = gdf[gdf["사업장명"] == nm].iloc[0]
                    if not (pd.isna(r.lon) or pd.isna(r.lat)):
                        snapped.append((r.lon, r.lat))
                except Exception as coord_error:
                    st.warning(f"⚠️ '{nm}' 좌표를 가져올 수 없습니다: {str(coord_error)}")

        if create_clicked and len(snapped) >= 2:
            try:
                segs, td, tl = [], 0.0, 0.0
                api_mode = "walking" if mode == "도보" else "driving"
                for i in range(len(snapped) - 1):
                    x1, y1 = snapped[i]
                    x2, y2 = snapped[i + 1]
                    coord = f"{x1},{y1};{x2},{y2}"
                    url = f"https://api.mapbox.com/directions/v5/mapbox/{api_mode}/{coord}"
                    params = {"geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN}
                    try:
                        r = requests.get(url, params=params, timeout=10)
                        if r.status_code == 200:
                            data_resp = r.json()
                            if data_resp.get("routes"):
                                route = data_resp["routes"][0]
                                segs.append(route["geometry"]["coordinates"])
                                td += route.get("duration", 0)
                                tl += route.get("distance", 0)
                            else:
                                st.warning(f"⚠️ 구간 {i + 1}의 경로를 찾을 수 없습니다.")
                        else:
                            st.warning(f"⚠️ API 호출 실패 (상태코드: {r.status_code})")
                    except requests.exceptions.Timeout:
                        st.warning("⚠️ API 호출 시간 초과")
                    except Exception as api_error:
                        st.warning(f"⚠️ API 호출 오류: {str(api_error)}")
                if segs:
                    st.session_state["order"] = stops
                    st.session_state["duration"] = td / 60
                    st.session_state["distance"] = tl / 1000
                    st.session_state["segments"] = segs
                    st.success("✅ 경로가 성공적으로 생성되었습니다!")
                    st.rerun()
                else:
                    st.error("❌ 모든 구간의 경로 생성에 실패했습니다.")
            except Exception as e:
                st.error(f"❌ 경로 생성 중 오류 발생: {str(e)}")
                st.info("💡 다른 출발지나 경유지를 선택해보세요.")

        # 🔧 지도 렌더링 (여백 없는 버전)
        try:
            m = folium.Map(
                location=[clat, clon],
                zoom_start=12,
                tiles="CartoDB Positron",
                prefer_canvas=True,
                control_scale=True
            )

            if boundary is not None:
                folium.GeoJson(
                    boundary,
                    style_function=lambda f: {"color": "#9aa0a6", "weight": 2, "dashArray": "4,4", "fillOpacity": 0.05}
                ).add_to(m)

            mc = MarkerCluster().add_to(m)

            # ✅ 회색 마커: 관광업/카페만 (자연경관은 따로 그립니다)
            for _, row in gdf[gdf["type"].isin(["관광업", "음식점/카페"])].iterrows():
                if not (pd.isna(row.lat) or pd.isna(row.lon)):
                    folium.Marker(
                        [row.lat, row.lon],
                        popup=folium.Popup(str(row["사업장명"]), max_width=200),
                        tooltip=str(row["사업장명"]),
                        icon=folium.Icon(color="gray")
                    ).add_to(mc)

            # ✅ 초록 마커: 자연경관 + 접근성 정보
            try:
                natural_df = gdf[gdf["type"] == "자연경관"]
                for _, row in natural_df.iterrows():
                    if not (pd.isna(row.lat) or pd.isna(row.lon)):
                        parking = str(row.get("장애인주차여부", "정보 없음"))
                        toilet = str(row.get("화장실", "정보 없음"))
                        wheel = str(row.get("휠체어대여", "정보 없음"))
                        braille = str(row.get("점자표시판", "정보 없음"))
                        acc_url = str(row.get("열린광장url", ""))

                        popup_html = f"""
                        <b>{row['사업장명']}</b><br>
                        유형: 자연경관<br>
                        🚗 장애인주차: {parking}<br>
                        ♿ 휠체어대여: {wheel}<br>
                        🚻 화장실: {toilet}<br>
                        🔤 점자표시판: {braille}<br>
                        <a href="{acc_url}" target="_blank">🔗 접근성 상세보기</a>
                        """

                        folium.Marker(
                            [row.lat, row.lon],
                            popup=folium.Popup(popup_html, max_width=280),
                            tooltip=f"🌿 {row['사업장명']}",
                            icon=folium.Icon(color="green", icon="leaf")
                        ).add_to(m)
            except Exception as e:
                st.warning(f"자연경관 표시 중 오류: {str(e)}")

            # 추천 경로 방문 순서 플래그 마커
            current_order = st.session_state.get("order", stops)
            for idx, (x, y) in enumerate(snapped, 1):
                place_name = current_order[idx - 1] if idx <= len(current_order) else f"지점 {idx}"
                folium.Marker(
                    [y, x],
                    icon=folium.Icon(color="red", icon="flag"),
                    tooltip=f"{idx}. {place_name}",
                    popup=folium.Popup(f"<b>{idx}. {place_name}</b>", max_width=200)
                ).add_to(m)

            # 경로선 시각화
            if st.session_state.get("segments"):
                palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04", "#9c27b0", "#ff9800"]
                segments = st.session_state["segments"]
                used_positions = []
                min_distance = 0.001
                for i, seg in enumerate(segments):
                    if seg:
                        folium.PolyLine(
                            [(pt[1], pt[0]) for pt in seg],
                            color=palette[i % len(palette)],
                            weight=5,
                            opacity=0.8
                        ).add_to(m)
                        mid = seg[len(seg) // 2]
                        candidate_pos = [mid[1], mid[0]]
                        while any(
                            abs(candidate_pos[0] - u[0]) < min_distance and abs(candidate_pos[1] - u[1]) < min_distance
                            for u in used_positions
                        ):
                            candidate_pos[0] += min_distance * 0.5
                            candidate_pos[1] += min_distance * 0.5
                        folium.map.Marker(
                            candidate_pos,
                            icon=DivIcon(
                                html=f"<div style='background:{palette[i % len(palette)]};"
                                     "color:#fff;border-radius:50%;width:28px;height:28px;"
                                     "line-height:28px;text-align:center;font-weight:600;"
                                     "box-shadow:0 2px 4px rgba(0,0,0,0.3);'>"
                                     f"{i + 1}</div>"
                            )
                        ).add_to(m)
                        used_positions.append(candidate_pos)
                try:
                    pts = [pt for seg in segments for pt in seg if seg]
                    if pts:
                        m.fit_bounds([[min(p[1] for p in pts), min(p[0] for p in pts)],
                                      [max(p[1] for p in pts), max(p[0] for p in pts)]])
                except:
                    m.location = [clat, clon]
                    m.zoom_start = 12
            else:
                m.location = [clat, clon]
                m.zoom_start = 12

            # 지도 출력
            st_folium(m, key="main_map", width=None, height=520, returned_objects=[], use_container_width=True)

        except Exception as map_error:
            st.error(f"❌ 지도 렌더링 오류: {str(map_error)}")
            st.info("지도를 불러올 수 없습니다.")

# ✅ OpenAI 클라이언트
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ✅ 생성형 AI 가이드
st.markdown("---")
st.markdown('<div class="section-header">🤖 생성형 AI기반 관광 가이드</div>', unsafe_allow_html=True)

if st.button("🔁 방문 순서 자동 입력"):
    st.session_state["auto_gpt_input"] = ", ".join(st.session_state.get("order", []))

if "messages" not in st.session_state:
    st.session_state["messages"] = []

with st.form("chat_form"):
    user_input = st.text_input(
        "관광지명을 쉼표로 구분해서 입력하거나 궁금한 것을 물어보세요 !",
        value=st.session_state.get("auto_gpt_input", "")
    )
    submitted = st.form_submit_button("🔍 관광지 정보 요청")

if submitted and user_input and client is not None:
    if st.session_state["order"]:
        st.markdown("---")
        st.markdown("## ✨ 관광지별 상세 정보")
        for place in st.session_state["order"][:3]:
            try:
                matched = data[data['t_name'].str.contains(place, na=False)]
            except Exception:
                matched = pd.DataFrame()

            # GPT 소개
            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "당신은 제주 지역의 관광지 및 카페, 식당을 간단하게 소개하는 관광 가이드입니다."},
                        {"role": "system", "content": "존댓말을 사용하세요."},
                        {"role": "user", "content": f"{place}를 두 문단 이내로 간단히 설명해주세요."}
                    ]
                )
                gpt_intro = response.choices[0].message.content
            except Exception as e:
                gpt_intro = f"❌ GPT 호출 실패: {place} 소개를 불러올 수 없어요. (오류: {str(e)})"

            score_text = ""; review_block = ""; cafe_info = ""
            if not matched.empty:
                try:
                    t_value = matched['t_value'].dropna().unique()
                    score_text = f"📊**관광지 평점**: ⭐ {t_value[0]}" if len(t_value) > 0 else ""
                    reviews = matched['t_review'].dropna().unique()
                    reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                    if reviews:
                        review_block = "\n".join([f'"{r}"' for r in reviews[:3]])
                    cafes = matched[['c_name', 'c_value', 'c_review']].drop_duplicates()
                    cafe_info = format_cafes(cafes)
                except Exception:
                    cafe_info = "데이터 처리 중 오류가 발생했습니다."
            else:
                cafe_info = ("현재 이 관광지 주변에 등록된 카페 정보는 없어요. \n"
                             "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니, \n"
                             "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊")

            st.markdown(f"### 🏛️ {place}")
            if score_text:
                st.markdown(score_text)
            st.markdown("#### ✨ 소개")
            st.markdown(gpt_intro.strip())
            if cafe_info:
                st.markdown("#### 🧋 주변 카페 추천")
                st.markdown(cafe_info.strip())
            if review_block:
                st.markdown("#### 💬 방문자 리뷰")
                for review in review_block.split("\n"):
                    if review.strip():
                        st.markdown(f"- {review.strip('\"')}")

elif submitted and user_input and client is None:
    st.error("❌ OpenAI 클라이언트가 초기화되지 않았습니다.")
