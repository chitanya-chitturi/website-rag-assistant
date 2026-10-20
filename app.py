"""
app.py - a small chat web page on top of ask.py.

Run:   streamlit run app.py
Opens in your browser at http://localhost:8501

The sidebar shows the chunks that were retrieved for your last question, with
their distance scores. Watching that panel is how you learn what retrieval
is really doing.
"""
import os

import streamlit as st

import config
from ask import answer_question

st.set_page_config(page_title="Site Q&A (RAG)", page_icon=":mag:")
st.title("Ask the site")
st.caption(f"Answers come only from pages indexed from: {', '.join(config.ALLOWED_PREFIXES)}")

has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
if not has_key:
    st.warning("ANTHROPIC_API_KEY is not set, so you'll see retrieved chunks and links "
               "but no written summary.")

if "history" not in st.session_state:
    st.session_state.history = []   # list of (role, markdown)
if "last_hits" not in st.session_state:
    st.session_state.last_hits = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

question = st.chat_input("e.g. How much paid time off do employees get?")
if question:
    st.session_state.history.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching and summarizing..."):
            answer, sources, hits = answer_question(question, use_llm=has_key)
        body = answer or "Here are the most relevant passages I found (see sidebar)."
        if sources:
            body += "\n\n**Sources**\n" + "\n".join(
                f"- [{s['title']} > {s['section']}]({s['link']})" for s in sources)
        st.markdown(body)
    st.session_state.history.append(("assistant", body))
    st.session_state.last_hits = hits

with st.sidebar:
    st.header("Retrieved chunks")
    st.caption("Lower distance = closer in meaning to your question.")
    for i, h in enumerate(st.session_state.last_hits, start=1):
        with st.expander(f"[{i}] {h['section'][:50]}  (distance {h['distance']})"):
            st.markdown(f"[{h['link']}]({h['link']})")
            st.text(h["text"])
