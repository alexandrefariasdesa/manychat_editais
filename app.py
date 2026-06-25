"""
ManyChat Editais — Dashboard de Efetividade dos Fluxos (standalone)
================================================================================
Mede o funil de 3 etapas de cada automação de EDITAL dentro do ManyChat:

    recebeu  →  entrou  →  engajou

- recebeu : a pessoa recebeu o fluxo do edital
- entrou  : clicou no botão de entrada (ex: "Verificar")
- engajou : clicou no conteúdo (ex: "Ver edital")

Fonte: aba `eventos_manychat` (gravada pelo Cloudflare Worker edital-flow-tracker).
Schema: ts | telefone | subscriber_id | edital | etapa

Sem conversão de venda, sem cruzamento com outras planilhas — tudo de dentro do
ManyChat. Pessoas distintas por etapa = chave por subscriber_id (fallback telefone).

Rodar:  streamlit run app.py
"""

import os
import unicodedata
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
def _cfg(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key)
        if val is not None:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)


GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SPREADSHEET_ID = _cfg("SPREADSHEET_ID", "")
EVENTOS_TAB = _cfg("EVENTOS_TAB", "eventos_manychat")

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Ordem do funil + rótulos
ETAPAS = ["recebeu", "entrou", "engajou"]
ETAPA_LABEL = {"recebeu": "Recebeu", "entrou": "Entrou", "engajou": "Engajou"}
ETAPA_COLOR = {"recebeu": "#636EFA", "entrou": "#00CC96", "engajou": "#19D3F3"}


# ──────────────────────────────────────────────────────────────────────────────
# Telefone (fallback de identidade quando não há subscriber_id)
# ──────────────────────────────────────────────────────────────────────────────
def normalize_phone(phone) -> str:
    s = str(phone or "").strip()
    if "." in s:
        s = s.split(".")[0]
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 12 and digits.startswith("55"):
        digits = digits[2:]
    return digits


def _canon_phone(phone: str) -> str:
    p = normalize_phone(phone)
    if len(p) == 10:
        return p[:2] + "9" + p[2:]  # normaliza pro formato com 9
    return p


def _person_key(row) -> str:
    """Identidade da pessoa: subscriber_id quando existe, senão telefone canônico."""
    sid = str(row.get("subscriber_id", "")).strip()
    if sid and sid.lower() not in ("", "nan", "none"):
        return f"s:{sid}"
    phone = _canon_phone(str(row.get("telefone", "")))
    return f"p:{phone}" if phone else ""


# ──────────────────────────────────────────────────────────────────────────────
# Google Sheets
# ──────────────────────────────────────────────────────────────────────────────
def _client():
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]), scopes=_SCOPES
            )
            return gspread.authorize(creds)
    except Exception:
        pass
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    return gspread.authorize(creds)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", str(s).strip().lower())


def _strip_tz(series: pd.Series) -> pd.Series:
    if series.dt.tz is not None:
        return series.dt.tz_localize(None)
    return series


@st.cache_data(ttl=300, show_spinner=False)
def get_eventos() -> pd.DataFrame:
    """Lê a aba `eventos_manychat`. Colunas: ts, telefone, subscriber_id, edital, etapa."""
    if not SPREADSHEET_ID:
        return pd.DataFrame()
    try:
        gc = _client()
        ws = gc.open_by_key(SPREADSHEET_ID).worksheet(EVENTOS_TAB)
        records = ws.get_all_records()
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df.columns = [_norm(c) for c in df.columns]

    # tolera nomes alternativos de coluna de timestamp
    for alt in ("ts", "clicado_em", "evento_em", "timestamp", "data"):
        if alt in df.columns:
            df = df.rename(columns={alt: "ts"})
            break

    for col in ("telefone", "subscriber_id", "edital", "etapa"):
        if col not in df.columns:
            df[col] = ""

    df["ts"] = _strip_tz(pd.to_datetime(df.get("ts"), errors="coerce", utc=True))
    df["edital"] = df["edital"].apply(_norm)
    df["etapa"] = df["etapa"].apply(_norm)
    df["subscriber_id"] = df["subscriber_id"].astype(str)
    df["telefone"] = df["telefone"].astype(str)
    df["pkey"] = df.apply(_person_key, axis=1)

    df = df[df["etapa"].isin(ETAPAS) & (df["edital"] != "") & (df["pkey"] != "")]
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Processamento — funil por edital
# ──────────────────────────────────────────────────────────────────────────────
def _edital_label(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").strip().title()


@st.cache_data(ttl=300, show_spinner=False)
def build_funis(start_date: date, end_date: date) -> dict:
    df = get_eventos()
    if df.empty:
        return {"resumo": pd.DataFrame(), "df": df}

    mask = (df["ts"].dt.date >= start_date) & (df["ts"].dt.date <= end_date)
    df = df[mask]
    if df.empty:
        return {"resumo": pd.DataFrame(), "df": df}

    # pessoas distintas por (edital, etapa)
    distintos = (
        df.groupby(["edital", "etapa"])["pkey"].nunique().reset_index(name="pessoas")
    )
    pivot = distintos.pivot(index="edital", columns="etapa", values="pessoas").fillna(0)
    for etapa in ETAPAS:
        if etapa not in pivot.columns:
            pivot[etapa] = 0
    pivot = pivot[ETAPAS].astype(int).reset_index()

    rows = []
    for _, r in pivot.iterrows():
        rec, ent, eng = int(r["recebeu"]), int(r["entrou"]), int(r["engajou"])
        rows.append({
            "edital": r["edital"],
            "Edital": _edital_label(r["edital"]),
            "Recebeu": rec,
            "Entrou": ent,
            "Engajou": eng,
            "Entrada (%)": round(ent / rec * 100, 1) if rec else 0.0,
            "Engajamento (%)": round(eng / ent * 100, 1) if ent else 0.0,
            "Funil total (%)": round(eng / rec * 100, 1) if rec else 0.0,
        })
    resumo = pd.DataFrame(rows).sort_values("Recebeu", ascending=False)
    return {"resumo": resumo, "df": df}


# ──────────────────────────────────────────────────────────────────────────────
# Auth — gate de senha simples (só ativa se `app_password` existir nos secrets)
# ──────────────────────────────────────────────────────────────────────────────
def _check_password() -> bool:
    """Bloqueia o dash atrás de uma senha. Sem `app_password` configurado
    (ex: rodando local), libera direto."""
    expected = _cfg("app_password", "")
    if not expected:
        return True
    if st.session_state.get("auth_ok"):
        return True

    st.markdown("### 🔒 Acesso restrito")
    pwd = st.text_input("Senha", type="password", key="_pwd")
    if pwd:
        if pwd == expected:
            st.session_state["auth_ok"] = True
            return True
        st.error("Senha incorreta.")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
def _br(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def main():
    st.set_page_config(page_title="ManyChat Editais — Funil", page_icon="📜", layout="wide")

    if not _check_password():
        st.stop()

    st.title("📜 Efetividade dos Fluxos de Edital — ManyChat")

    if not SPREADSHEET_ID:
        st.error(
            "Falta configurar `SPREADSHEET_ID` (planilha nova dos editais). "
            "Preencha o `.env` (ou `.streamlit/secrets.toml`) e recarregue. "
            "A planilha precisa estar compartilhada como Editor com o service account."
        )
        st.stop()

    with st.sidebar:
        st.header("Período")
        hoje = date.today()
        preset = st.radio(
            "Atalho",
            ["Últimos 7 dias", "Últimos 30 dias", "Últimos 90 dias", "Personalizado"],
            index=1,
        )
        if preset == "Últimos 7 dias":
            start, end = hoje - timedelta(days=6), hoje
        elif preset == "Últimos 30 dias":
            start, end = hoje - timedelta(days=29), hoje
        elif preset == "Últimos 90 dias":
            start, end = hoje - timedelta(days=89), hoje
        else:
            rng = st.date_input("Intervalo", value=(hoje - timedelta(days=29), hoje))
            start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (rng, rng))
        st.caption(f"De **{start:%d/%m/%Y}** até **{end:%d/%m/%Y}**")
        if st.button("🔄 Atualizar dados"):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Carregando eventos da planilha..."):
        data = build_funis(start, end)

    resumo: pd.DataFrame = data.get("resumo", pd.DataFrame())

    if resumo is None or resumo.empty:
        st.info(
            "Nenhum evento no período. Confirme que o Worker `edital-flow-tracker` "
            "está gravando na aba `eventos_manychat` e que há fluxos rodando no intervalo."
        )
        st.stop()

    st.caption(
        "Funil por edital: **Recebeu** (recebeu o fluxo) → **Entrou** (clicou no botão "
        "de entrada, ex: \"Verificar\") → **Engajou** (clicou no conteúdo). "
        "Pessoas distintas por etapa. Entrada % = entrou ÷ recebeu · Engajamento % = "
        "engajou ÷ entrou."
    )

    # ── Totais ──
    tot_rec = int(resumo["Recebeu"].sum())
    tot_ent = int(resumo["Entrou"].sum())
    tot_eng = int(resumo["Engajou"].sum())
    r_ent = (tot_ent / tot_rec * 100) if tot_rec else 0.0
    r_eng = (tot_eng / tot_ent * 100) if tot_ent else 0.0
    r_tot = (tot_eng / tot_rec * 100) if tot_rec else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Editais ativos", _br(len(resumo)))
    c2.metric("Receberam", _br(tot_rec))
    c3.metric("Entraram", _br(tot_ent))
    c4.metric("Engajaram", _br(tot_eng))
    c5.metric("Entrada", f"{r_ent:.1f}%")
    c6.metric("Engajamento", f"{r_eng:.1f}%")

    st.divider()

    # ── Funil (geral ou por edital) ──
    st.subheader("Funil")
    opts = ["Todos (geral)"] + resumo["Edital"].tolist()
    escolha = st.selectbox("Escolha o edital", opts, index=0)
    if escolha == "Todos (geral)":
        fx = [tot_rec, tot_ent, tot_eng]
    else:
        r = resumo[resumo["Edital"] == escolha].iloc[0]
        fx = [int(r["Recebeu"]), int(r["Entrou"]), int(r["Engajou"])]
    fig_fun = go.Figure(go.Funnel(
        y=["Recebeu", "Entrou", "Engajou"],
        x=fx, textinfo="value+percent initial",
        marker={"color": [ETAPA_COLOR[e] for e in ETAPAS]},
    ))
    fig_fun.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=280)
    st.plotly_chart(fig_fun, use_container_width=True)

    st.divider()

    # ── Comparação entre editais ──
    st.subheader("Comparação entre Editais")
    col_l, col_r = st.columns(2)
    with col_l:
        fig = px.bar(
            resumo, x="Edital", y=["Recebeu", "Entrou", "Engajou"],
            barmode="group",
            color_discrete_sequence=[ETAPA_COLOR[e] for e in ETAPAS],
            labels={"value": "Pessoas", "variable": ""}, text_auto=True,
        )
        fig.update_layout(legend_title_text="", margin=dict(l=0, r=0, t=10, b=0), height=380)
        st.plotly_chart(fig, use_container_width=True)
    with col_r:
        st.dataframe(
            resumo.drop(columns=["edital"]).style.format({
                "Entrada (%)": "{:.1f}%",
                "Engajamento (%)": "{:.1f}%",
                "Funil total (%)": "{:.1f}%",
            }),
            use_container_width=True, hide_index=True, height=380,
        )

    # ── Ranking por engajamento ──
    st.markdown("**Engajamento (%) por edital** — engajou ÷ entrou")
    rank = resumo.sort_values("Engajamento (%)", ascending=True)
    fig_rk = go.Figure(go.Bar(
        x=rank["Engajamento (%)"], y=rank["Edital"], orientation="h",
        marker_color="#19D3F3",
        text=[f"{v:.1f}%" for v in rank["Engajamento (%)"]], textposition="outside",
    ))
    fig_rk.update_layout(
        margin=dict(l=0, r=0, t=10, b=0), height=max(260, 40 * len(rank)),
        xaxis=dict(title="Engajamento (%)", ticksuffix="%", rangemode="tozero"),
    )
    st.plotly_chart(fig_rk, use_container_width=True)


if __name__ == "__main__":
    main()
