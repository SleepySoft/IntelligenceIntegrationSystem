import streamlit as st
import json
import os
import pandas as pd

# ================= 配置 =================
DATA_FILE = "eval_results.jsonl"
REVIEWED_FILE = "eval_reviewed.jsonl"

st.set_page_config(layout="wide", page_title="Model Evaluation Tool")


# streamlit run validation2_review_app.py


# --- Helper Functions ---
def load_data():
    data = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line))
    return data


def save_progress(index, label, comment, current_data):
    # 更新内存中的数据
    current_data[index]['human_label'] = label
    current_data[index]['comments'] = comment

    # 追加/覆盖写入文件 (这里简单处理：每次全部重写，数据量不大时没问题)
    # 实际生产中建议 Append 模式或数据库
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        for entry in current_data:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- Main App Logic ---
def main():
    st.title("🤖 LLM Fine-tuning Human Reviewer")

    # 1. 初始化 Session State
    if 'data' not in st.session_state:
        st.session_state.data = load_data()

    if 'current_index' not in st.session_state:
        # 找到第一个还没评审的数据 (human_label is None)
        unreviewed_indices = [i for i, d in enumerate(st.session_state.data) if d.get('human_label') is None]
        st.session_state.current_index = unreviewed_indices[0] if unreviewed_indices else 0

    data = st.session_state.data
    idx = st.session_state.current_index

    # 进度条
    reviewed_count = sum(1 for d in data if d.get('human_label') is not None)
    total_count = len(data)
    st.progress(reviewed_count / total_count if total_count > 0 else 0)
    st.caption(f"Progress: {reviewed_count}/{total_count}")

    if idx < total_count:
        item = data[idx]

        # --- 界面布局 ---
        st.subheader(f"Sample #{idx + 1}")

        # 输入展示区 (折叠以节省空间)
        with st.expander("Input Prompt / Instruction", expanded=True):
            st.info(f"**Instruction:** {item['instruction']}")
            st.text(f"**Input:** {item['input']}")

        # 对比区 (左右两栏)
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### ✅ Ground Truth (期望值)")
            st.success(item['ground_truth'])

        with col2:
            st.markdown("### 🤖 Model Output (实际值)")
            # 如果有 score，可以用正则高亮显示
            st.warning(item['model_output'])

            st.info(f"🤖 AI Judge Score: {item.get('judge_score', 'N/A')}/10")
            st.caption(f"Reasoning: {item.get('judge_reasoning', '')}")

        # --- 操作区 ---
        st.divider()
        c1, c2, c3 = st.columns([1, 1, 4])

        with c1:
            if st.button("👍 Good / Pass", use_container_width=True, type="primary"):
                save_progress(idx, "pass", "", data)
                st.session_state.current_index += 1
                st.rerun()

        with c2:
            if st.button("👎 Bad / Fail", use_container_width=True):
                save_progress(idx, "fail", "", data)
                st.session_state.current_index += 1
                st.rerun()

        with c3:
            # 允许写备注
            comment = st.text_input("Optional Comments (e.g. 'Hallucination', 'Wrong Score')", key="comment_input")
            if st.button("Submit with Comment"):
                save_progress(idx, "commented", comment, data)
                st.session_state.current_index += 1
                st.rerun()

        # 导航按钮
        st.divider()
        prev, _, next_btn = st.columns([1, 8, 1])
        if prev.button("Previous"):
            st.session_state.current_index = max(0, idx - 1)
            st.rerun()
        if next_btn.button("Next"):
            st.session_state.current_index = min(len(data) - 1, idx + 1)
            st.rerun()

    else:
        st.balloons()
        st.success("🎉 All samples reviewed! You can calculate the accuracy now.")

        # --- 修复 KeyError 的部分 ---
        if data:
            df = pd.DataFrame(data)

            # 1. 安全检查：确保列存在
            if 'human_label' in df.columns:
                st.write("### Label Distribution")
                # 统计各标签数量
                counts = df['human_label'].value_counts()
                st.write(counts)

                # 可选：简单的可视化
                st.bar_chart(counts)
            else:
                st.info("No labels found yet (all items are unreviewed or missing 'human_label' field).")
        else:
            st.warning("No data loaded.")
        # ---------------------------

        # 下载最终结果
        st.download_button(
            label="Download Reviewed JSONL",
            data=json.dumps(data, indent=2, ensure_ascii=False),
            file_name="reviewed_final.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()
