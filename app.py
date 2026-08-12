# ==============================================================================
# GJU Faculty Self-Evaluation Dashboard (Streamlit + GitHub)
# ==============================================================================
# ملاحظات للمطور (بالعربي):
#   هذا التطبيق يستنسخ واجهة وميزات نسخة HTML المستقلة (Dashboard + Settings &
#   Rules) لكنه يعمل كتطبيق Streamlit عادي، ومصمم للنشر على Streamlit
#   Community Cloud مربوطاً بمستودع GitHub.
#
#   طريقة الحفظ:
#     - "Session only": التعديلات تُحفظ فقط في st.session_state لهذه الجلسة
#       ولا تُكتب على القرص أو GitHub - تنعكس فوراً على أي نتائج معروضة.
#     - "Permanent": بالإضافة لتطبيقها فوراً، تُكتب rules.csv و settings.json
#       محلياً على القرص، وإذا كانت أسرار GitHub مُهيَّأة (انظر أسفل) يتم أيضاً
#       Commit مباشر لهذين الملفين على المستودع، بحيث تبقى هذه هي الإعدادات
#       المعتمدة حتى بعد إعادة نشر التطبيق (Streamlit Cloud يعيد استنساخ
#       المستودع بالكامل عند كل إعادة تشغيل/نشر).
#
#   ملفات "Factory" (factory_rules.csv / factory_settings.json) هي نسخة
#   احتياطية غير قابلة للتعديل من داخل التطبيق - تُستخدم فقط كشبكة أمان عبر
#   زر "Revert to Original Factory Defaults" في تبويب الإعدادات.
#
#   لتفعيل الكتابة المباشرة على GitHub، أضيفي إلى ملف .streamlit/secrets.toml
#   (أو Secrets الخاصة بـ Streamlit Community Cloud):
#
#       [github]
#       token  = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # Personal Access Token بصلاحية repo
#       repo   = "your-org/your-repo-name"
#       branch = "main"
#
#   بدون هذه الأسرار، سيعمل التطبيق بشكل طبيعي تماماً، وسيُحفظ الحفظ "الدائم"
#   محلياً فقط على قرص السيرفر الحالي (يبقى فعالاً طالما لم تُعاد عملية النشر).
# ==============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import difflib
import os
import json
import base64
import tempfile
from datetime import datetime, timezone

import matplotlib.pyplot as plt
from fpdf import FPDF
import requests

# ------------------------------------------------------------------------------
# 0. FILE PATHS & CONSTANTS
# ------------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(APP_DIR, "rules.csv")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
FACTORY_RULES_FILE = os.path.join(APP_DIR, "factory_rules.csv")
FACTORY_SETTINGS_FILE = os.path.join(APP_DIR, "factory_settings.json")
LOGO_FILE = os.path.join(APP_DIR, "gju_logo.png")

# تسميات العرض لكل بعد (Dimension) - غيّري النص هنا فقط لتغيير الاسم الظاهر
# للمستخدم دون التأثير على المفتاح الداخلي (teaching/research/innovation/service)
# المستخدَم في حسابات محرك التحليل.
DIMENSION_LABELS = {
    "teaching": "Teaching",
    "research": "Research",
    "innovation": "Innovation and Links with the Industry",
    "service": "Service",
    "general": "General (not scored)",
}


def dim_label(key):
    return DIMENSION_LABELS.get(str(key or "").strip().lower(), key)


# قيمة "Innovation" أصبحت الآن بصيغتها الكاملة "Innovation and links with the
# industry" - وهي القيمة الفعلية التي تُخزَّن في ملف rules.csv وتظهر في القائمة
# المنسدلة، وليست مجرد تسمية عرض (dim_label لا يزال يُستخدَم بصيغة Title Case
# منسّقة في التقارير وعناوين الأعمدة عبر canonicalize_dimension).
DIMENSION_OPTIONS = ["Teaching", "Research", "Innovation and links with the industry", "Service", "General"]
DIMENSION_OPTION_LABELS = [dim_label(d) for d in DIMENSION_OPTIONS]

RULE_CODES = [
    "YESNO", "RATING_5", "MCQ_3LEVEL", "MCQ_5LEVEL",
    "NUM_NORM_COURSES", "NUM_NORM_STUDENTS", "NUM_NORM_SUPERVISION",
    "NUM_NORM_FUNDING", "NUM_NORM_PUBLICATIONS", "NUM_NORM_SCOPUS",
    "MCQ_CUSTOM", "KEEP_TEXT",
]


def canonicalize_dimension(d):
    """
    يوحّد أي صياغة لاسم البعد (مثل "Innovation" أو "Innovation and links with
    the industry") إلى مفتاحه الداخلي القصير. هذا يجعل مطابقة الأسئلة بأبعادها
    غير حساسة لصياغة نص "Dimension" في ملف القواعد - دون أي تغيير في معادلات
    حساب الدرجات نفسها.
    """
    n = str(d or "").strip().lower()
    if "teaching" in n:
        return "teaching"
    if "research" in n:
        return "research"
    if "innovation" in n:
        return "innovation"
    if "service" in n:
        return "service"
    return "general"


# ------------------------------------------------------------------------------
# 1. GITHUB PERSISTENCE (اختياري - يتفعّل فقط عند توفر الأسرار)
# ------------------------------------------------------------------------------
def github_configured():
    try:
        gh = st.secrets["github"]
        return bool(gh.get("token")) and bool(gh.get("repo"))
    except Exception:
        return False


def _github_conf():
    gh = st.secrets["github"]
    return gh["token"], gh["repo"], gh.get("branch", "main")


def github_commit_file(path_in_repo, content_str, commit_message):
    """
    يكتب/يحدّث ملفاً واحداً في مستودع GitHub عبر GitHub REST API (Contents API).
    يتطلب Personal Access Token بصلاحية repo مخزَّن في st.secrets["github"]["token"].
    يُعيد (True, None) عند النجاح أو (False, رسالة الخطأ) عند الفشل.
    """
    if not github_configured():
        return False, "GitHub secrets are not configured."
    try:
        token, repo, branch = _github_conf()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        api_url = f"https://api.github.com/repos/{repo}/contents/{path_in_repo}"

        # 1) نحصل على sha الحالي للملف (إن وُجد) - مطلوب لتحديث ملف موجود مسبقاً
        sha = None
        get_resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")
        elif get_resp.status_code not in (404,):
            return False, f"GitHub GET failed ({get_resp.status_code}): {get_resp.text[:200]}"

        # 2) نرسل التحديث (أو الإنشاء) للملف
        b64_content = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
        payload = {"message": commit_message, "content": b64_content, "branch": branch}
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            return True, None
        return False, f"GitHub PUT failed ({put_resp.status_code}): {put_resp.text[:200]}"
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------------------
# 2. LOAD / SAVE SETTINGS (rules + weights + thresholds + bonus)
# ------------------------------------------------------------------------------
def _rules_df_to_records(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    records = []
    for _, row in df.iterrows():
        records.append({
            "dimension": str(row.get("Dimension", "")).strip(),
            "qno": str(row.get("Question no", "")).strip(),
            "question": str(row.get("Question text", "")).strip(),
            "answerType": str(row.get("Answer_Type", "")).strip(),
            "ruleCode": str(row.get("Rule_Code", "")).strip(),
            "expected": str(row.get("Expected_Response_and_Score", "")).strip(),
            "importance": float(row.get("Importance", 0) or 0),
        })
    return records


def _records_to_rules_df(records):
    return pd.DataFrame([{
        "Dimension": r["dimension"],
        "Question no": r["qno"],
        "Question text": r["question"],
        "Answer_Type": r["answerType"],
        "Rule_Code": r["ruleCode"],
        "Expected_Response_and_Score": r["expected"],
        "Importance": r["importance"],
    } for r in records])


def load_rules_csv(path):
    df = pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="skip")
    return _rules_df_to_records(df)


def load_settings_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_full_settings(rules_path, settings_path):
    return {
        "rules": load_rules_csv(rules_path),
        **load_settings_json(settings_path),
    }


def factory_defaults():
    return load_full_settings(FACTORY_RULES_FILE, FACTORY_SETTINGS_FILE)


def active_defaults():
    """القواعد/الإعدادات الفعّالة حالياً على القرص (rules.csv + settings.json) -
    وهي ما يقرأه التطبيق افتراضياً عند بدء أي جلسة جديدة."""
    try:
        return load_full_settings(RULES_FILE, SETTINGS_FILE)
    except Exception:
        return factory_defaults()


def write_settings_locally(settings):
    """يكتب rules.csv و settings.json محلياً على قرص السيرفر الحالي فوراً."""
    rules_df = _records_to_rules_df(settings["rules"])
    rules_df.to_csv(RULES_FILE, index=False, encoding="utf-8-sig")
    payload = {
        "weights": settings["weights"],
        "thresholds": settings["thresholds"],
        "bonus": settings["bonus"],
    }
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def commit_settings_to_github(settings, message):
    """يكتب rules.csv/settings.json محلياً، ثم (إن كانت الأسرار متوفرة) يرفعهما
    مباشرة كـ commit إلى مستودع GitHub - هذا هو "الحفظ الدائم الحقيقي" الذي يبقى
    فعّالاً حتى بعد إعادة نشر التطبيق على Streamlit Community Cloud."""
    write_settings_locally(settings)
    if not github_configured():
        return False, "GitHub secrets not configured — saved locally only (may be lost on redeploy)."

    rules_df = _records_to_rules_df(settings["rules"])
    rules_csv_str = rules_df.to_csv(index=False)
    settings_json_str = json.dumps({
        "weights": settings["weights"],
        "thresholds": settings["thresholds"],
        "bonus": settings["bonus"],
    }, ensure_ascii=False, indent=2)

    ok1, err1 = github_commit_file("rules.csv", rules_csv_str, message + " (rules.csv)")
    ok2, err2 = github_commit_file("settings.json", settings_json_str, message + " (settings.json)")
    if ok1 and ok2:
        return True, None
    return False, " | ".join([e for e in (err1, err2) if e])


# ------------------------------------------------------------------------------
# 3. SCORING FUNCTIONS — محرك التحليل (منقول من النسخة الأصلية بدون أي تعديل
#    على منطقه الحسابي، فقط دعم لصياغات متعددة لاسم البعد - انظر
#    canonicalize_dimension أعلاه)
# ------------------------------------------------------------------------------
def score_yesno(response):
    if pd.isna(response):
        return np.nan
    return 100 if "yes" in str(response).strip().lower() else 0


def score_rating_5(response):
    if pd.isna(response):
        return np.nan
    res = str(response).strip().lower()
    if "outstanding" in res:
        return 100
    if "very good" in res:
        return 80
    if "good" in res:
        return 60
    if "satisfactory" in res:
        return 40
    if "requires improvement" in res:
        return 20
    return 0


def score_mcq_3level(response):
    if pd.isna(response):
        return np.nan
    res = str(response).strip().lower()
    if any(x in res for x in ["fully", "leading", "both"]):
        return 100
    if "external" in res:
        return 85
    if "partially" in res or "internal" in res:
        return 70
    return 0


def score_mcq_5level(response):
    if pd.isna(response):
        return np.nan
    res = str(response).strip().lower()
    if any(x in res for x in ["publishing", "patent", "industrial"]):
        return 100
    if "conference" in res or "coference" in res:
        return 85
    if "other" in res:
        return 70
    return 0


def score_numeric_benchmark(value, benchmark):
    if pd.isna(value):
        return np.nan
    try:
        val = float(value)
        if not benchmark:
            return 0
        return min((val / benchmark) * 100, 100)
    except (ValueError, TypeError):
        return 0


def extract_benchmark(expected_str):
    try:
        return float(str(expected_str).split(":")[1].strip())
    except (IndexError, ValueError):
        return 1


def score_for_rule(rule_code, response, expected_str):
    rc = str(rule_code or "").strip().upper()
    if rc == "KEEP_TEXT":
        return response
    if rc == "YESNO":
        return score_yesno(response)
    if rc == "RATING_5":
        return score_rating_5(response)
    if rc == "MCQ_3LEVEL":
        return score_mcq_3level(response)
    if rc == "MCQ_5LEVEL":
        return score_mcq_5level(response)
    if rc.startswith("NUM_NORM"):
        return score_numeric_benchmark(response, extract_benchmark(expected_str))
    return response  # قواعد غير معروفة/مخصصة (مثل MCQ_CUSTOM) تُحفظ كنص خام


def get_bonus(position, bonus_table):
    if pd.isna(position):
        return 0
    pos = str(position).strip().lower()
    for entry in bonus_table:
        if str(entry["position"]).strip().lower() == pos:
            return float(entry["bonus"])
    for entry in bonus_table:
        key = str(entry["position"]).strip().lower()
        if key and key in pos:
            return float(entry["bonus"])
    return 0


# ------------------------------------------------------------------------------
# 4. CORE ANALYSIS ENGINE — لم يتم تعديل أي منطق حسابي هنا (نفس ما في النسخة
#    السابقة وفي نسخة HTML)، فقط مطابقة أكثر مرونة لأسماء الأبعاد.
# ------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def process_evaluation(df_raw, rules, weights, high_thresh, low_thresh, bonus_table):
    rule_questions = [r["question"].strip() for r in rules]

    def find_best_match(target):
        matches = difflib.get_close_matches(target, rule_questions, n=1, cutoff=0.65)
        return matches[0] if matches else None

    col_to_rule = {}
    for raw_col in df_raw.columns:
        clean = str(raw_col).strip()
        match = find_best_match(clean)
        if match:
            rule = next((r for r in rules if r["question"].strip() == match), None)
            if rule:
                col_to_rule[raw_col] = rule

    info_fields = ["Name", "Email", "Employee No.", "School", "Department",
                    "Date of appointment", "Academic Rank"]
    info_col_map = {}
    for field in info_fields:
        matching_cols = [c for c in df_raw.columns if field.lower() in str(c).lower()]
        if matching_cols:
            info_col_map[field] = matching_cols[0]

    admin_col = None
    for c in df_raw.columns:
        cl = str(c).lower()
        if "select" in cl and "administrative" in cl:
            admin_col = c
            break
    if admin_col is None:
        for c in df_raw.columns:
            if "administrative" in str(c).lower() or "8." in str(c):
                admin_col = c
                break

    # درجة كل سؤال مُطابَق، لكل صف
    scores_by_row = []
    for _, row in df_raw.iterrows():
        scored = {}
        for raw_col, rule in col_to_rule.items():
            scored[rule["question"].strip()] = score_for_rule(rule["ruleCode"], row[raw_col], rule["expected"])
        scores_by_row.append(scored)

    dims = list(weights.keys())
    results = []
    for i, (_, row) in enumerate(df_raw.iterrows()):
        rec = {}
        for field, col in info_col_map.items():
            rec[field] = row[col]
        rec["Position_Title"] = str(row[admin_col]) if admin_col is not None and pd.notna(row[admin_col]) else "None"
        rec["Admin_Bonus"] = get_bonus(row[admin_col], bonus_table) if admin_col is not None else 0

        base_total = 0.0
        for dim in dims:
            dim_rules = [r for r in rules if canonicalize_dimension(r["dimension"]) == canonicalize_dimension(dim)]
            sum_product, sum_importance = 0.0, 0.0
            for r in dim_rules:
                q = r["question"].strip()
                importance = float(r["importance"] or 0)
                scored = scores_by_row[i].get(q)
                try:
                    num = float(scored)
                    valid = not pd.isna(num)
                except (TypeError, ValueError):
                    valid = False
                if valid:
                    sum_product += num * importance
                    sum_importance += importance
            dim_score = (sum_product / sum_importance) if sum_importance > 0 else 0.0
            rec[f"{dim.capitalize()} Score (100)"] = round(dim_score, 2)
            base_total += dim_score * float(weights.get(dim, 0) or 0)

        rec["Base_Score (100)"] = round(base_total, 2)
        rec["Total_Final_Score"] = round(base_total + float(rec["Admin_Bonus"] or 0), 2)
        results.append(rec)

    results_df = pd.DataFrame(results)

    # ------------------------------------------------------------------------------
    # إزالة التكرار: بعض أعضاء هيئة التدريس قاموا بتعبئة نموذج التقييم أكثر من
    # مرة، مما ينتج عنه أكثر من صف لنفس العضو ويؤثر على المتوسط العام والترتيب
    # (Percentile) لأن العضو يُحتسب أكثر من مرة. لذلك، قبل حساب النسبة المئوية
    # والتصنيف، نحدد العضو المكرر عبر "Employee No." (أو "Email"، أو "Name" كبديل
    # عند غياب رقم الموظف)، ونُبقي فقط الصف صاحب أعلى Total_Final_Score لكل
    # عضو، ونستبعد بقية الصفوف المكررة له.
    if not results_df.empty:
        dedup_field = None
        for candidate in ("Employee No.", "Email", "Name"):
            if candidate in results_df.columns and results_df[candidate].astype(str).str.strip().ne("").any():
                dedup_field = candidate
                break

        if dedup_field is not None:
            key_series = results_df[dedup_field].astype(str).str.strip().str.lower()
            has_key = key_series.ne("")
            with_key = results_df[has_key].copy()
            without_key = results_df[~has_key].copy()  # صفوف بلا معرّف صالح -> لا يمكن تحديد تكرارها فتبقى كما هي
            if not with_key.empty:
                with_key["_dedup_key"] = key_series[has_key]
                with_key = (
                    with_key.sort_values("Total_Final_Score", ascending=False)
                    .drop_duplicates(subset="_dedup_key", keep="first")
                    .drop(columns="_dedup_key")
                )
            results_df = pd.concat([with_key, without_key], ignore_index=True)

    if not results_df.empty:
        results_df["Percentile"] = results_df["Total_Final_Score"].rank(pct=True) * 100
        results_df["Percentile"] = results_df["Percentile"].round(2)

        def classify(pct):
            if pct >= high_thresh:
                return "Exceeds Expectations"
            if pct <= low_thresh:
                return "Needs Attention"
            return "Meets Expectations"

        results_df["Performance_Category"] = results_df["Percentile"].apply(classify)

    return results_df, dims


# ------------------------------------------------------------------------------
# 5. PDF ENGINE — شعار أكبر مضمَّن، بدون إشارة % في أي مؤشر، أسماء أبعاد ودّية
# ------------------------------------------------------------------------------
def fmt_num(x):
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return "-"


class GJU_PDF(FPDF):
    def header(self):
        if os.path.exists(LOGO_FILE):
            self.image(LOGO_FILE, 10, 8, 34)  # شعار أكبر (كان 30 سابقاً)
        self.set_font("Helvetica", "B", 14)
        self.set_xy(46, 10)
        self.cell(0, 10, "German Jordanian University - Evaluation Report", 0, 1, "L")
        self.set_xy(46, 20)
        self.set_font("Helvetica", size=11)
        self.ln(14)


def create_global_pdf(df, dims):
    pdf = GJU_PDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "Executive Summary", ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"Total Participants: {len(df)}", ln=True)
    pdf.cell(0, 8, f"University Average Score: {fmt_num(df['Total_Final_Score'].mean())}", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(0, 10, "Top 5 Performers", ln=True, fill=True, border=1)

    pdf.set_font("Helvetica", "B", 10)
    col_widths = [15, 65, 30, 25, 55]
    headers = ["Rank", "Name", "Emp No.", "Score", "Category"]
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 8, h, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", size=10)
    top_5 = df.nlargest(5, "Total_Final_Score")
    for idx, (_, row) in enumerate(top_5.iterrows(), start=1):
        name = str(row.get("Name", "Unknown"))[:35]
        emp_no = str(row.get("Employee No.", "N/A"))
        score = fmt_num(row.get("Total_Final_Score", 0))
        cat = str(row.get("Performance_Category", "N/A"))
        pdf.cell(col_widths[0], 8, str(idx), border=1, align="C")
        pdf.cell(col_widths[1], 8, name, border=1)
        pdf.cell(col_widths[2], 8, emp_no, border=1, align="C")
        pdf.cell(col_widths[3], 8, score, border=1, align="C")
        pdf.cell(col_widths[4], 8, cat, border=1, align="C")
        pdf.ln()

    pdf.ln(10)
    if pdf.get_y() > 160:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(0, 10, "Performance Distribution", ln=True, fill=True, border=1)
    pdf.ln(5)

    counts = df["Performance_Category"].value_counts()
    color_map = {"Exceeds Expectations": "#1e8e4e", "Meets Expectations": "#1a56b0", "Needs Attention": "#e08a00"}
    colors = [color_map.get(x, "#9e9e9e") for x in counts.index]

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.pie(counts.values, labels=counts.index, colors=colors, autopct="%1.1f%%", startangle=140)
    ax.set_title("Category Distribution")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
        fig.savefig(tmpfile.name, format="png", bbox_inches="tight")
        plt.close(fig)
        pdf.image(tmpfile.name, x=50, w=100)
    os.remove(tmpfile.name)
    pdf.ln(5)

    if pdf.get_y() > 250:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(80, 8, "Category", border=1, align="C", fill=True)
    pdf.cell(40, 8, "Count", border=1, align="C", fill=True)
    pdf.cell(40, 8, "Share", border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", size=10)
    total_count = len(df)
    for cat, count in counts.items():
        pct = (count / total_count) * 100 if total_count else 0
        pdf.cell(80, 8, str(cat), border=1)
        pdf.cell(40, 8, str(count), border=1, align="C")
        pdf.cell(40, 8, fmt_num(pct), border=1, align="C")
        pdf.ln()

    return bytes(pdf.output())


def create_individual_pdf(row, dims):
    pdf = GJU_PDF()
    pdf.add_page()

    clean_name = str(row.get("Name", "Unknown"))
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Performance Report: {clean_name}", ln=True)
    pdf.ln(5)

    raw_date = row.get("Date of appointment", "N/A")
    try:
        formatted_date = pd.to_datetime(raw_date).strftime("%Y-%m-%d") if pd.notna(raw_date) else "N/A"
    except Exception:
        formatted_date = str(raw_date)

    pdf.set_font("Helvetica", size=11)
    data_points = [
        ("Employee No.", row.get("Employee No.", "N/A")),
        ("School", str(row.get("School", "N/A"))),
        ("Department", str(row.get("Department", "N/A"))),
        ("Appointment Date", formatted_date),
        ("Position Title", str(row.get("Position_Title", "None"))),
        ("Category", row.get("Performance_Category", "N/A")),
    ]
    for label, val in data_points:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(50, 10, f"{label}:", 0)
        pdf.set_font("Helvetica", size=11)
        pdf.cell(0, 10, str(val), 0, 1)

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(180, 10, "Detailed Dimension Scores", ln=True, fill=True, border=1, align="C")

    dimensions = {dim_label(d): row.get(f"{d.capitalize()} Score (100)", 0) for d in dims}

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 8, "Dimension", border=1, align="C", fill=True)
    pdf.cell(90, 8, "Score", border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", size=10)
    for dim_name, score in dimensions.items():
        pdf.cell(90, 8, dim_name, border=1)
        pdf.cell(90, 8, fmt_num(score), border=1, align="C")
        pdf.ln()

    pdf.ln(5)
    best_dim = max(dimensions, key=dimensions.get)
    best_score = dimensions[best_dim]

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(0, 102, 0)
    pdf.multi_cell(0, 8, f"Strengths: Your best performing area is '{best_dim}' with a score of {fmt_num(best_score)}")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(90, 10, "Base Score (Weighted):", border=1, fill=True)
    pdf.cell(90, 10, fmt_num(row.get("Base_Score (100)", 0)), border=1, align="C", ln=True)

    pdf.cell(90, 10, "Administrative Bonus:", border=1, fill=True)
    pdf.cell(90, 10, "+" + fmt_num(row.get("Admin_Bonus", 0)), border=1, align="C", ln=True)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(90, 12, "Total Final Score:", border=1, fill=True)
    pdf.cell(90, 12, fmt_num(row.get("Total_Final_Score", 0)), border=1, align="C", ln=True)

    return bytes(pdf.output())


# ------------------------------------------------------------------------------
# 6. STREAMLIT UI
# ------------------------------------------------------------------------------
st.set_page_config(page_title="GJU Faculty Self-Evaluation Dashboard", layout="wide")

st.markdown("""
<style>
  .block-container{padding-top:1.2rem;}
  .gju-header{background:linear-gradient(135deg,#00457c,#002f57);color:#fff;
    padding:16px 22px;border-radius:10px;display:flex;align-items:center;gap:18px;margin-bottom:18px;}
  .gju-header h1{margin:0;font-size:20px;}
  .gju-header h2{margin:2px 0 0;font-size:13px;font-weight:400;opacity:.9;}
  .metric-note{font-size:12px;color:#6b7785;}
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# 6.1 ACCESS PASSWORD GATE
# ------------------------------------------------------------------------------
# يحمي هذا القسم التطبيق بالكامل بكلمة مرور واحدة قبل عرض أي محتوى (الداشبورد
# أو الإعدادات). كلمة المرور تُقرأ من Streamlit Secrets فقط (لا تُكتب أبداً في
# الكود المصدري) - يجب ضبطها على Streamlit Community Cloud عبر:
#   Settings -> Secrets:
#       [auth]
#       password = "اكتبي_كلمة_مرور_قوية_هنا"
#
# إذا لم يتم ضبط [auth].password في Secrets، يبقى التطبيق مفتوحاً لأي شخص
# لديه الرابط (لتجنّب قفل التطبيق بالخطأ أثناء التطوير) مع تنبيه واضح لذلك.
def _get_app_password():
    try:
        return st.secrets["auth"]["password"]
    except Exception:
        return None


def check_password():
    correct_password = _get_app_password()

    # لا توجد كلمة مرور مضبوطة بعد -> اسمحي بالدخول مع تنبيه للمسؤول فقط
    if not correct_password:
        st.warning(
            "⚠️ No access password is configured (st.secrets['auth']['password']). "
            "This app is currently open to anyone with the link. "
            "Add a password in Secrets to restrict access."
        )
        return True

    if st.session_state.get("authenticated", False):
        return True

    st.markdown(
        """
        <div style="max-width:480px;margin:70px auto 18px;padding:26px 30px;border-radius:12px;
                    background:#f4f7fb;border:1px solid #d7e0ea;">
          <h3 style="color:#00457c;margin-top:0;">🔒 GJU Faculty Self-Evaluation Dashboard</h3>
          <p style="color:#33414f;font-size:14px;line-height:1.6;">
            This dashboard is restricted to authorized personnel.<br>
            For access, please contact <b>Eng. Waed Alswaeer</b> at
            <a href="mailto:waed.alswaer@gju.edu.jo">waed.alswaer@gju.edu.jo</a>.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1.4, 1])
    with col2:
        pwd = st.text_input("Password", type="password", key="_access_pwd_input")
        entered = st.button("Enter", use_container_width=True)
        if entered:
            if pwd == correct_password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again or contact waed.alswaer@gju.edu.jo.")
    return False


if not check_password():
    st.stop()

# ---- Session state initialization -------------------------------------------------
if "settings" not in st.session_state:
    st.session_state.settings = active_defaults()
if "persist_mode" not in st.session_state:
    st.session_state.persist_mode = "Session only"
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "results_dims" not in st.session_state:
    st.session_state.results_dims = None
if "last_raw_df" not in st.session_state:
    st.session_state.last_raw_df = None

# ---- Header -------------------------------------------------------------------------
header_cols = st.columns([1, 6])
with header_cols[0]:
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=140)  # شعار أكبر في الواجهة (كان أصغر سابقاً)
with header_cols[1]:
    st.markdown("""
    <div class="gju-header" style="background:none;color:#00457c;padding:0;">
      <div>
        <h1 style="color:#00457c;">German Jordanian University</h1>
        <h2 style="color:#4a5a6a;">Institutional Faculty Self-Evaluation Analysis Dashboard</h2>
      </div>
    </div>
    """, unsafe_allow_html=True)

tab_dashboard, tab_settings = st.tabs(["📊 Dashboard", "⚙️ Settings & Rules"])


# ------------------------------------------------------------------------------
# helper: يعيد حساب النتائج على آخر ملف مرفوع، بنفس فكرة recomputeFromCurrentSettings
# في نسخة HTML - يُستخدَم بعد أي تعديل على الإعدادات لتحديث Dashboard فوراً
# دون طلب رفع/تحليل الملف من جديد.
# ------------------------------------------------------------------------------
def recompute_results():
    if st.session_state.last_raw_df is None:
        return
    s = st.session_state.settings
    df, dims = process_evaluation(
        st.session_state.last_raw_df, s["rules"], s["weights"],
        float(s["thresholds"]["high"]), float(s["thresholds"]["low"]), s["bonus"],
    )
    st.session_state.results_df = df
    st.session_state.results_dims = dims


def weights_total_ok(weights):
    return round(sum(float(v) for v in weights.values()), 2) == 1.0


# ==============================================================================
# TAB 1: DASHBOARD
# ==============================================================================
with tab_dashboard:
    st.subheader("1) Upload Faculty Responses File (Excel export from MS Forms)")
    uploaded = st.file_uploader("Excel file (.xlsx)", type=["xlsx", "xls"], label_visibility="collapsed")

    run_col, _ = st.columns([1, 3])
    with run_col:
        run_clicked = st.button("Run Analysis & Generate Reports", type="primary", use_container_width=True,
                                 disabled=uploaded is None)

    if run_clicked and uploaded is not None:
        s = st.session_state.settings
        if not weights_total_ok(s["weights"]):
            st.error("⚠ Cannot proceed: total dimension weights must equal 1.00 (see Settings & Rules tab).")
        else:
            df_raw = pd.read_excel(uploaded)
            st.session_state.last_raw_df = df_raw
            recompute_results()
            st.success(f"Analysis completed successfully — {len(df_raw)} records.")

    results_df = st.session_state.results_df
    dims = st.session_state.results_dims

    if results_df is not None and not results_df.empty:
        st.subheader("2) Institutional Results")
        n = len(results_df)
        avg = results_df["Total_Final_Score"].mean()
        counts = results_df["Performance_Category"].value_counts()

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Participants", n)
        m2.metric("University Average Score", fmt_num(avg))  # بدون %
        m3.metric("Exceeds Expectations", int(counts.get("Exceeds Expectations", 0)))
        m4.metric("Meets Expectations", int(counts.get("Meets Expectations", 0)))
        m5.metric("Needs Attention", int(counts.get("Needs Attention", 0)))

        chart_col, legend_col = st.columns([1, 1])
        with chart_col:
            color_map = {"Exceeds Expectations": "#1e8e4e", "Meets Expectations": "#1a56b0", "Needs Attention": "#e08a00"}
            fig, ax = plt.subplots(figsize=(4, 4))
            colors = [color_map.get(c, "#9e9e9e") for c in counts.index]
            ax.pie(counts.values, labels=counts.index, colors=colors, autopct="%1.1f%%", startangle=140)
            st.pyplot(fig, use_container_width=False)

        pdf_col1, pdf_col2 = st.columns(2)
        with pdf_col1:
            st.download_button(
                "Download Institutional Report (PDF)",
                data=create_global_pdf(results_df, dims),
                file_name="GJU_Final_Report.pdf", mime="application/pdf",
            )
        with pdf_col2:
            st.download_button(
                "Export Results (CSV)",
                data=results_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="GJU_Evaluation_Results.csv", mime="text/csv",
            )

        # ---- 3) Aggregated performance view ----------------------------------------
        st.subheader("3) Aggregated Performance View (for management decisions)")
        st.caption("Compare average performance across Schools, Departments, Academic Ranks or Performance "
                    "Categories to spot outliers and support institutional decisions. This view only re-groups "
                    "the results already computed above — it does not change any scoring rule.")
        agg_field_options = [c for c in ["School", "Department", "Academic Rank", "Performance_Category"]
                              if c in results_df.columns or c == "Performance_Category"]
        agg_field = st.selectbox("Group by", agg_field_options, key="agg_field")
        if agg_field in results_df.columns:
            grouped = results_df.copy()
            grouped[agg_field] = grouped[agg_field].fillna("(Not specified)").replace("", "(Not specified)")
            dim_cols = [f"{d.capitalize()} Score (100)" for d in (dims or [])]
            agg_table = grouped.groupby(agg_field).agg(
                Participants=("Total_Final_Score", "count"),
                **{f"Avg Total Score": ("Total_Final_Score", "mean")},
                **{f"Avg {dim_label(d)}": (f"{d.capitalize()} Score (100)", "mean") for d in (dims or [])},
            ).reset_index().sort_values("Avg Total Score", ascending=False)
            for col in agg_table.columns:
                if agg_table[col].dtype.kind in "fc":
                    agg_table[col] = agg_table[col].round(1)
            st.bar_chart(agg_table.set_index(agg_field)["Avg Total Score"])
            st.dataframe(agg_table, use_container_width=True, hide_index=True)
            st.download_button(
                "Export Aggregated View (CSV)",
                data=agg_table.to_csv(index=False).encode("utf-8-sig"),
                file_name="GJU_Aggregated_View.csv", mime="text/csv",
            )

        # ---- 4) Detailed sortable table ----------------------------------------------
        st.subheader("4) Detailed Results Table")
        st.caption("Click any column header to sort ascending / descending. Use the search box to filter rows.")
        search = st.text_input("Search by name / employee no. / department...", "")
        display_df = results_df.copy()
        display_cols = ["Name", "Employee No.", "School", "Department"] + \
            [f"{d.capitalize()} Score (100)" for d in (dims or [])] + \
            ["Base_Score (100)", "Admin_Bonus", "Total_Final_Score", "Percentile", "Performance_Category"]
        display_cols = [c for c in display_cols if c in display_df.columns]
        display_df = display_df[display_cols].rename(columns={
            f"{d.capitalize()} Score (100)": f"{dim_label(d)} Score" for d in (dims or [])
        })
        if search:
            mask = pd.Series(False, index=display_df.index)
            for col in ["Name", "Employee No.", "Department", "School"]:
                if col in display_df.columns:
                    mask = mask | display_df[col].astype(str).str.contains(search, case=False, na=False)
            display_df = display_df[mask]
        # إزالة إشارة % - الأعمدة الرقمية تُعرض كأرقام فقط، وst.dataframe يسمح
        # بالفرز تصاعدياً/تنازلياً بالنقر على أي عنوان عمود بشكل مدمج
        num_cols = display_df.select_dtypes(include=[np.number]).columns
        st.dataframe(
            display_df.style.format({c: "{:.1f}" for c in num_cols}),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"{len(display_df)} record(s)")

        # ---- 5) Individual report ----------------------------------------------------
        st.subheader("5) Individual Faculty Report")
        results_df = results_df.reset_index(drop=True)
        options = [f"{row.get('Name','Unknown')} ({row.get('Employee No.','N/A')})"
                   for _, row in results_df.iterrows()]
        sel_idx = st.selectbox("Select Faculty Member", options=range(len(options)),
                                format_func=lambda i: options[i])
        sel_row = results_df.iloc[sel_idx]

        info_col, dim_col = st.columns(2)
        with info_col:
            # نحوّل كل القيم إلى نص صريح - عمود مختلط بين نصوص وأرقام (numpy.int64)
            # يُسبب خطأ تحويل داخلي في Streamlit/PyArrow عند عرضه بـ st.table
            st.table(pd.DataFrame({
                "Field": ["Name", "Employee No.", "School", "Department", "Category"],
                "Value": [str(sel_row.get("Name", "") or ""), str(sel_row.get("Employee No.", "") or ""),
                          str(sel_row.get("School", "") or ""), str(sel_row.get("Department", "") or ""),
                          str(sel_row.get("Performance_Category", "") or "")],
            }).set_index("Field"))
        with dim_col:
            dim_rows = [(dim_label(d), fmt_num(sel_row.get(f"{d.capitalize()} Score (100)", 0))) for d in (dims or [])]
            dim_rows += [
                ("Base Score", fmt_num(sel_row.get("Base_Score (100)", 0))),
                ("Admin Bonus", "+" + fmt_num(sel_row.get("Admin_Bonus", 0))),
                ("Total Final Score", fmt_num(sel_row.get("Total_Final_Score", 0))),
            ]
            st.table(pd.DataFrame(dim_rows, columns=["Dimension", "Score"]).set_index("Dimension"))

        st.download_button(
            f"Download Individual PDF Report",
            data=create_individual_pdf(sel_row, dims),
            file_name=f"Report_{sel_row.get('Employee No.', 'NA')}.pdf", mime="application/pdf",
        )
    else:
        st.info("Upload an Excel file above and click 'Run Analysis & Generate Reports' to see results here.")


# ==============================================================================
# TAB 2: SETTINGS & RULES
# ==============================================================================
with tab_settings:
    s = st.session_state.settings

    # ---- Flash message ---------------------------------------------------------------
    # ملاحظة مهمة: st.success/st.warning/st.error التي تُستدعى مباشرة قبل
    # st.rerun() تُفقَد فوراً لأن rerun يعيد رسم الصفحة من جديد قبل أن يراها
    # المستخدم. لهذا نخزّن رسالة "flash" في session_state ثم نعرضها هنا في أول
    # الصفحة بعد إعادة التحميل، ونحذفها فوراً بعد العرض لمرة واحدة فقط.
    flash = st.session_state.pop("flash", None)
    if flash:
        getattr(st, flash["type"])(flash["text"])

    # ---- Save mode ------------------------------------------------------------------
    st.radio(
        "Save changes",
        options=["Session only", "Permanent"],
        key="persist_mode",
        horizontal=True,
        help="'Session only' applies changes instantly to any results shown above without writing to disk. "
             "'Permanent' also writes rules.csv/settings.json to disk and, if GitHub secrets are configured, "
             "commits them directly to the repository so they remain the default after the app is redeployed.",
    )
    if github_configured():
        st.caption("✅ GitHub commit is configured — 'Permanent' saves will be committed directly to the repository.")
    else:
        st.caption("⚠ GitHub secrets are not configured — 'Permanent' saves will only be written locally on this "
                   "server and may be lost after the app is redeployed. See the comment at the top of app.py for "
                   "how to configure `st.secrets['github']`.")

    # ---- Thresholds -------------------------------------------------------------------
    st.markdown("### Performance Classification Thresholds (based on Percentile)")
    th_col1, th_col2 = st.columns(2)
    with th_col1:
        high_thresh = st.number_input("Exceeds Expectations — Percentile ≥", 0.0, 100.0,
                                       float(s["thresholds"]["high"]), step=1.0)
    with th_col2:
        low_thresh = st.number_input("Needs Attention — Percentile ≤", 0.0, 100.0,
                                      float(s["thresholds"]["low"]), step=1.0)

    # ---- Weights ------------------------------------------------------------------------
    st.markdown("### Dimension Weights — total must equal 1.00")
    w_col1, w_col2, w_col3, w_col4 = st.columns(4)
    with w_col1:
        w_teaching = st.slider("Teaching", 0.0, 1.0, float(s["weights"]["teaching"]), step=0.05)
    with w_col2:
        w_research = st.slider("Research", 0.0, 1.0, float(s["weights"]["research"]), step=0.05)
    with w_col3:
        w_innovation = st.slider(dim_label("innovation"), 0.0, 1.0, float(s["weights"]["innovation"]), step=0.05)
    with w_col4:
        w_service = st.slider("Service", 0.0, 1.0, float(s["weights"]["service"]), step=0.05)

    total_weights = round(w_teaching + w_research + w_innovation + w_service, 2)
    if total_weights == 1.0:
        st.success(f"✔ Total weights: {total_weights:.2f}")
    else:
        st.error(f"⚠ Total weights must equal 1.00 — current: {total_weights:.2f}")

    # ---- Administrative bonus table --------------------------------------------------
    st.markdown("### Administrative Position Bonus Table")
    bonus_df = pd.DataFrame(s["bonus"])
    bonus_df = bonus_df.rename(columns={"position": "Administrative Position Title", "bonus": "Bonus Value"})
    edited_bonus_df = st.data_editor(
        bonus_df, num_rows="dynamic", use_container_width=True, key="bonus_editor",
        column_config={
            "Administrative Position Title": st.column_config.TextColumn(required=True),
            "Bonus Value": st.column_config.NumberColumn(step=0.5),
        },
    )

    # ---- Scoring rules table (with a Dimension dropdown per row) ---------------------
    st.markdown("### Question Scoring Rules — Dimensions, Questions, Scoring Method, Importance")
    st.caption("These rules are used to fuzzy-match the columns of the uploaded Excel file to a known question, "
               "decide how to score each answer, and weight it within its dimension. Use the 'Dimension' dropdown "
               "on each row to move a question into a different category.")
    rules_df = _records_to_rules_df(s["rules"])
    # نعرض المستخدم قيمة Dimension بصياغتها القصيرة القياسية في القائمة المنسدلة
    # (Teaching/Research/Innovation/Service/General) بدل أي صياغة نصية طويلة،
    # عبر تحويلها للمفتاح القياسي وقت العرض فقط (بدون تعديل بيانات الصف حتى
    # يغيّرها المستخدم بنفسه من القائمة).
    rules_df["Dimension"] = rules_df["Dimension"].apply(
        lambda d: next((opt for opt in DIMENSION_OPTIONS if canonicalize_dimension(opt) == canonicalize_dimension(d)), d)
    )
    edited_rules_df = st.data_editor(
        rules_df, num_rows="dynamic", use_container_width=True, height=420, key="rules_editor",
        column_config={
            "Dimension": st.column_config.SelectboxColumn(options=DIMENSION_OPTIONS, required=True),
            "Rule_Code": st.column_config.SelectboxColumn(options=RULE_CODES, required=True),
            "Importance": st.column_config.NumberColumn(step=1.0),
        },
    )
    st.caption("Available scoring rules: YESNO, RATING_5, MCQ_3LEVEL, MCQ_5LEVEL, NUM_NORM_* (with a Benchmark), "
               "KEEP_TEXT (kept as text only, not scored).")

    # ---- Apply settings ------------------------------------------------------------------
    st.markdown("### Settings Management")

    def collect_settings_from_editors():
        bonus_records = [
            {"position": str(r["Administrative Position Title"]).strip(), "bonus": float(r["Bonus Value"] or 0)}
            for _, r in edited_bonus_df.iterrows() if str(r["Administrative Position Title"]).strip()
        ]
        rules_records = _rules_df_to_records(edited_rules_df)
        return {
            "weights": {"teaching": w_teaching, "research": w_research,
                        "innovation": w_innovation, "service": w_service},
            "thresholds": {"high": high_thresh, "low": low_thresh},
            "rules": rules_records,
            "bonus": bonus_records,
        }

    def set_flash(msg_type, text):
        st.session_state.flash = {"type": msg_type, "text": text}

    apply_col, reset_col = st.columns(2)
    with apply_col:
        if st.button("Apply Settings", type="primary", use_container_width=True):
            new_settings = collect_settings_from_editors()
            if not weights_total_ok(new_settings["weights"]):
                set_flash("error", "⚠ Total dimension weights must equal 1.00 before applying — please fix the weights above.")
            else:
                st.session_state.settings = new_settings
                if st.session_state.persist_mode == "Permanent":
                    ok, err = commit_settings_to_github(new_settings, "Update evaluation settings via Streamlit app")
                    if ok:
                        set_flash("success", "Settings saved permanently and committed to GitHub, and applied to the results above.")
                    else:
                        write_settings_locally(new_settings)
                        set_flash("warning", f"Saved locally on this server, but GitHub commit did not complete: {err}")
                else:
                    set_flash("success", "Settings applied for this session only, and reflected in the results above.")
                recompute_results()
            st.rerun()

    with reset_col:
        if st.button("Restore Defaults", use_container_width=True):
            st.session_state.settings = active_defaults()
            set_flash("success", "Defaults restored and applied to the results above.")
            recompute_results()
            st.rerun()

    # ---- Administrator controls ------------------------------------------------------
    st.markdown("---")
    st.markdown("#### 🔒 Administrator Controls — Permanently Disable Original Rules")
    st.caption("Use this only if the administration has asked you to permanently replace the original (factory) "
               "scoring rules with the settings currently shown above. This commits directly to the `rules.csv` "
               "and `settings.json` files used by everyone who opens this app.")

    admin_col1, admin_col2 = st.columns(2)
    with admin_col1:
        if st.button("Set Current Settings as Permanent Institutional Default", use_container_width=True):
            new_settings = collect_settings_from_editors()
            if not weights_total_ok(new_settings["weights"]):
                set_flash("error", "⚠ Total dimension weights must equal 1.00 before setting a permanent institutional default.")
            else:
                ok, err = commit_settings_to_github(
                    new_settings, "Set new permanent institutional default evaluation rules"
                )
                st.session_state.settings = new_settings
                if ok:
                    set_flash("success", "The original rules have been permanently disabled — these settings are now "
                               "the institutional default committed to GitHub for everyone using this app.")
                else:
                    set_flash("warning", f"Saved locally on this server, but GitHub commit did not complete: {err}")
                recompute_results()
            st.rerun()

    with admin_col2:
        if st.button("Revert to Original Factory Defaults (safety net)", use_container_width=True):
            factory = factory_defaults()
            ok, err = commit_settings_to_github(factory, "Revert evaluation rules to original factory defaults")
            st.session_state.settings = factory
            if ok:
                set_flash("success", "Original factory rules restored as the institutional baseline on GitHub.")
            else:
                set_flash("warning", f"Saved locally on this server, but GitHub commit did not complete: {err}")
            recompute_results()
            st.rerun()
