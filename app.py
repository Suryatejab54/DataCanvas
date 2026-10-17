"""DataCanvas: explore, visualize, query, and export tabular data."""

from __future__ import annotations

import os
import re
from io import BytesIO, StringIO

import duckdb
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="DataCanvas", page_icon="📊", layout="wide")


def load_data(file_bytes: bytes, filename: str, sheet: str | None = None) -> pd.DataFrame:
    """Read uploaded data, making CSV encoding and Excel sheet selection explicit."""
    if filename.lower().endswith(".csv"):
        try:
            return pd.read_csv(BytesIO(file_bytes))
        except UnicodeDecodeError:
            return pd.read_csv(BytesIO(file_bytes), encoding="latin-1")
    return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet or 0)


def safe_sql_query(query: str, df: pd.DataFrame) -> pd.DataFrame:
    """Run one read-only query against the registered `data` table."""
    cleaned = query.strip().rstrip(";").strip()
    if not re.match(r"^(SELECT|WITH)\b", cleaned, re.IGNORECASE):
        raise ValueError("Only SELECT queries (including CTEs beginning with WITH) are allowed.")
    if ";" in cleaned:
        raise ValueError("Please run one query at a time.")
    con = duckdb.connect(
        database=":memory:",
        config={
            "enable_external_access": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )
    try:
        con.register("data", df)
        return con.execute(cleaned).df()
    finally:
        con.close()


def dataframe_context(df: pd.DataFrame) -> str:
    """Compact, bounded context for the optional AI assistant."""
    profile = pd.DataFrame({
        "dtype": df.dtypes.astype(str),
        "non_null": df.notna().sum(),
        "missing": df.isna().sum(),
        "unique": df.nunique(dropna=True),
    })
    numeric = df.describe().round(3).to_string() if len(df.select_dtypes(include="number").columns) else "No numeric columns."
    sample = df.head(8).to_csv(index=False)
    return f"Shape: {df.shape}\nColumn profile:\n{profile.to_string()}\n\nNumeric summary:\n{numeric}\n\nFirst rows (CSV):\n{sample}"


def make_report(df: pd.DataFrame, filename: str) -> str:
    missing = df.isna().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    numeric = df.describe().round(2).to_string() if len(df.select_dtypes(include="number").columns) else "No numeric columns."
    missing_text = missing.to_string() if not missing.empty else "No missing values."
    return f"""# Data overview: {filename}

## Dataset
- Rows: {len(df):,}
- Columns: {df.shape[1]:,}
- Duplicate rows: {int(df.duplicated().sum()):,}
- Memory: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB

## Column types and completeness
{dataframe_context(df).split('Numeric summary:')[0].split('Column profile:')[1]}

## Missing values
{missing_text}

## Numeric summary
{numeric}
"""


st.title("📊 DataCanvas")
st.caption("Upload a spreadsheet to profile, explore, visualize, query, and export your data.")

with st.sidebar:
    st.header("Data source")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not groq_api_key:
        try:
            groq_api_key = st.secrets.get("GROQ_API_KEY", "")
        except Exception:
            groq_api_key = ""
    if not groq_api_key:
        groq_api_key = st.text_input("Groq API key (optional)", type="password", help="Used only for AI questions. You can use every other feature without a key.")

if not uploaded:
    st.info("Upload a CSV or Excel file in the sidebar to begin.")
    st.markdown("**Included tools:** data quality checks, interactive filters, charts, read-only SQL, AI questions, and CSV/Markdown exports.")
    st.stop()

file_bytes = uploaded.getvalue()
sheet_name = None
if uploaded.name.lower().endswith((".xlsx", ".xls")):
    try:
        workbook = pd.ExcelFile(BytesIO(file_bytes))
        sheet_name = st.sidebar.selectbox("Worksheet", workbook.sheet_names)
    except Exception as exc:
        st.error(f"Could not read this Excel file: {exc}")
        st.stop()

try:
    original_df = load_data(file_bytes, uploaded.name, sheet_name)
except Exception as exc:
    st.error(f"Could not load this file: {exc}")
    st.stop()

if original_df.empty or original_df.shape[1] == 0:
    st.warning("The selected file has no data to analyze.")
    st.stop()

st.sidebar.header("Filters")
filtered_df = original_df.copy()
with st.sidebar.expander("Filter rows", expanded=False):
    for index, column in enumerate(original_df.columns):
        series = original_df[column]
        if pd.api.types.is_numeric_dtype(series) and series.notna().any():
            low, high = float(series.min()), float(series.max())
            if low < high:
                selected = st.slider(f"{column}", low, high, (low, high), key=f"num_{index}")
                filtered_df = filtered_df[filtered_df[column].between(*selected) | filtered_df[column].isna()]
        elif series.nunique(dropna=True) <= 30:
            options = sorted(series.dropna().unique().tolist(), key=str)
            chosen = st.multiselect(f"{column}", options, default=options, key=f"cat_{index}")
            if len(chosen) < len(options):
                filtered_df = filtered_df[filtered_df[column].isin(chosen) | filtered_df[column].isna()]

if filtered_df.empty:
    st.warning("No rows match the current filters. Adjust them in the sidebar.")
    st.stop()

total_rows, total_cols = original_df.shape
missing_count = int(original_df.isna().sum().sum())
dupes = int(original_df.duplicated().sum())
cards = st.columns(5)
cards[0].metric("Rows", f"{len(filtered_df):,}", delta=f"of {total_rows:,}")
cards[1].metric("Columns", f"{total_cols:,}")
cards[2].metric("Missing cells", f"{missing_count:,}")
cards[3].metric("Duplicate rows", f"{dupes:,}")
cards[4].metric("Memory", f"{original_df.memory_usage(deep=True).sum()/1024**2:.1f} MB")

tab_overview, tab_explore, tab_charts, tab_sql, tab_ai, tab_export = st.tabs(
    ["Overview", "Explore", "Visualize", "SQL", "Ask AI", "Export"]
)

with tab_overview:
    st.subheader("Data preview")
    st.dataframe(filtered_df.head(100), width="stretch", hide_index=True)
    left, right = st.columns(2)
    with left:
        st.subheader("Column quality")
        quality = pd.DataFrame({
            "Type": original_df.dtypes.astype(str),
            "Non-null": original_df.notna().sum(),
            "Missing": original_df.isna().sum(),
            "Missing %": (original_df.isna().mean() * 100).round(1),
            "Unique": original_df.nunique(dropna=True),
        })
        st.dataframe(quality, width="stretch")
    with right:
        st.subheader("Missing values by column")
        missing_by_col = original_df.isna().sum().sort_values(ascending=False)
        missing_by_col = missing_by_col[missing_by_col > 0]
        if missing_by_col.empty:
            st.success("No missing values found.")
        else:
            fig, ax = plt.subplots(figsize=(9, max(3, len(missing_by_col) * 0.3)))
            missing_by_col.sort_values().plot.barh(ax=ax, color="#4169e1")
            ax.set_xlabel("Missing cells")
            ax.set_ylabel("")
            st.pyplot(fig)
            plt.close(fig)

with tab_explore:
    st.subheader("Summary statistics")
    st.dataframe(filtered_df.describe(include="all").transpose(), width="stretch")
    st.subheader("Value counts")
    category = st.selectbox("Choose a column", original_df.columns, key="counts_column")
    counts = filtered_df[category].value_counts(dropna=False).head(50).rename_axis(category).reset_index(name="Count")
    st.dataframe(counts, width="stretch", hide_index=True)

with tab_charts:
    st.subheader("Build a chart")
    chart_kind = st.selectbox("Chart", ["Bar", "Line", "Scatter", "Histogram", "Box plot"])
    numeric_cols = filtered_df.select_dtypes(include="number").columns.tolist()
    if chart_kind in ("Scatter", "Line"):
        if len(numeric_cols) < 2:
            st.info("This chart needs at least two numeric columns.")
        else:
            x_col, y_col = st.columns(2)
            x = x_col.selectbox("X axis", numeric_cols, key="chart_x")
            y = y_col.selectbox("Y axis", [c for c in numeric_cols if c != x], key="chart_y")
            fig, ax = plt.subplots(figsize=(9, 4.5))
            if chart_kind == "Scatter":
                ax.scatter(filtered_df[x], filtered_df[y], alpha=0.65, color="#4169e1")
                ax.set_xlabel(str(x))
                ax.set_ylabel(str(y))
            else:
                ordered = filtered_df[[x, y]].dropna().sort_values(x)
                ax.plot(ordered[x], ordered[y], color="#4169e1")
                ax.set_xlabel(str(x))
                ax.set_ylabel(str(y))
            ax.grid(alpha=0.2)
            fig.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
    elif chart_kind in ("Histogram", "Box plot"):
        if not numeric_cols:
            st.info("This chart needs a numeric column.")
        else:
            col = st.selectbox("Numeric column", numeric_cols, key="dist_col")
            fig, ax = plt.subplots(figsize=(9, 4))
            if chart_kind == "Histogram":
                filtered_df[col].dropna().hist(ax=ax, bins=30, color="#4169e1", edgecolor="white")
                ax.set_xlabel(col)
                ax.set_ylabel("Count")
            else:
                filtered_df[[col]].plot(kind="box", ax=ax)
            st.pyplot(fig)
            plt.close(fig)
    else:
        col = st.selectbox("Category", original_df.columns, key="bar_col")
        top_n = st.slider("Top categories", 5, 30, 15)
        category_counts = (
            filtered_df[col]
            .astype("string")
            .fillna("(missing)")
            .value_counts()
            .head(top_n)
            .rename_axis("Category")
            .reset_index(name="Count")
        )
        fig, ax = plt.subplots(figsize=(9, max(3, len(category_counts) * 0.3)))
        ax.barh(category_counts["Category"].astype(str), category_counts["Count"], color="#4169e1")
        ax.set_xlabel("Count")
        ax.set_ylabel("")
        ax.invert_yaxis()
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

with tab_sql:
    st.subheader("Read-only SQL")
    st.caption("Query the filtered dataset as `data`. Only one SELECT or WITH query is accepted.")
    sql = st.text_area("SQL query", "SELECT * FROM data LIMIT 10", height=130)
    if st.button("Run query", type="primary"):
        try:
            result = safe_sql_query(sql, filtered_df)
            st.success(f"Returned {len(result):,} rows.")
            st.dataframe(result, width="stretch", hide_index=True)
            st.download_button("Download query results (CSV)", result.to_csv(index=False), "query_results.csv", "text/csv")
        except Exception as exc:
            st.error(f"Query failed: {exc}")

with tab_ai:
    st.subheader("Ask a question about your data")
    if not groq_api_key:
        st.info("Add a Groq API key in the sidebar or set GROQ_API_KEY in your environment to enable AI questions.")
    else:
        question = st.text_area("Question", placeholder="Which products have the highest average sales?", height=90)
        if st.button("Analyze", type="primary", key="ask_ai") and question.strip():
            try:
                from langchain_groq import ChatGroq

                llm = ChatGroq(groq_api_key=groq_api_key, model="openai/gpt-oss-20b", temperature=0)
                prompt = f"""You are a careful business data analyst. Answer using only the provided dataset context. If the context cannot support an answer, say so. Do not claim you ran code or queried rows beyond this context. Give concise, practical answers and mention uncertainty.

DATA CONTEXT:
{dataframe_context(filtered_df)}

QUESTION: {question}"""
                with st.spinner("Analyzing the dataset summary…"):
                    answer = llm.invoke(prompt)
                st.markdown(answer.content)
            except Exception as exc:
                st.error(f"AI request failed: {exc}")
    st.caption("AI receives a small sample and aggregate statistics from the currently filtered data. Avoid uploading sensitive information.")

with tab_export:
    st.subheader("Export your work")
    st.download_button("Download filtered data (CSV)", filtered_df.to_csv(index=False), "filtered_data.csv", "text/csv", type="primary")
    st.download_button("Download data profile (Markdown)", make_report(original_df, uploaded.name), "data_report.md", "text/markdown")
    st.caption("Exports use the current filters for data, while the profile summarizes the original uploaded dataset.")
