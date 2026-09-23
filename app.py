import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pathlib import Path
from bs4 import BeautifulSoup

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "mistralai/mistral-small-3.2-24b-instruct"

def load_knowledge():
    folder = Path(__file__).parent / "knowledge"
    parts = []
    for f in sorted(folder.glob("*.txt")):
        title = f.stem.replace("_", " ").title()
        parts.append(f"## {title}\n{f.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Pages the bot should read. Add or remove pages here.
KNOWLEDGE_PAGES = [
    ("Home", "/"),
    ("Why AVATAR?", "/why-avatar"),
    ("About", "/about"),
    ("Fun Facts", "/fun-facts"),
    ("Dorm Facts", "/dorm-rankings"),
]
MAX_CHARS_PER_PAGE = 6000

SYSTEM_PROMPT_BASE = (
    "You are AVATAR AI, the assistant for AVATAR (Advancing Villanova to Energy "
    "Autonomy and Resiliency), a Villanova University capstone website about campus "
    "energy, load forecasting, what-if scenarios, sustainability, and resiliency. "
    "Keep answers short (under 120 words) and clear. Respond in plain text with no "
    "markdown.\n\n"
    "How to answer:\n"
    "1. For questions about this website, AVATAR, or Villanova's specific numbers, "
    "use the SITE KNOWLEDGE below, using its exact figures and showing any math "
    "briefly. If the site knowledge doesn't contain the answer, say you don't have "
    "that information. Never invent Villanova-specific figures.\n"
    "2. For general questions about energy, sustainability, or related technology "
    "(for example: what a battery energy storage system (BESS), peak demand, or a "
    "microgrid is), answer from your own general knowledge in plain language, and "
    "make clear you're giving general information rather than site data.\n"
    "3. If a question is unrelated to energy, sustainability, or this site, "
    "politely steer back to those topics."
)


def page_text(path):
    """Render one of our own pages and return just its readable text."""
    response = app.test_client().get(path)
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    for tag in soup.select("#chat-toggle, #chat-panel"):
        tag.decompose()
    root = soup.find("main") or soup.body or soup
    return root.get_text("\n", strip=True)[:MAX_CHARS_PER_PAGE]


def load_page_knowledge():
    parts = []
    for title, path in KNOWLEDGE_PAGES:
        text = page_text(path)
        if text:
            parts.append(f"## {title} page\n{text}")
    return "\n\n".join(parts)


_prompt_cache = {}


def get_system_prompt():
    # Cached after first use; rebuilt on every message while debug=True
    # so edits to your pages show up without restarting.
    if "prompt" not in _prompt_cache or app.debug:
        _prompt_cache["prompt"] = (
            SYSTEM_PROMPT_BASE
            + "\n\nSITE KNOWLEDGE:\n"
            + load_page_knowledge()
            + "\n\n"
            + load_knowledge()
        )
    return _prompt_cache["prompt"]

NAV_ITEMS = [
    ("dashboard", "Dashboard"),
    ("load_forecasting", "Load Forecasting"),
    ("what_ifs", "What Ifs"),
    ("why_avatar", "Why AVATAR?"),
    ("about", "About"),
    ("fun_facts", "Fun Facts"),
    ("dorm_rankings", "Dorm Facts"),
]


@app.context_processor
def inject_navigation():
    return {"nav_items": NAV_ITEMS}


@app.route("/")
def home():
    return render_template("home.html", active_page="home")


@app.route("/dashboard")
def dashboard():
    demo_metrics = [
        {"label": "Campus energy use", "value": "Demo data", "detail": "Connect this card to Villanova meter data."},
        {"label": "Peak demand", "value": "Demo data", "detail": "Show campus peak demand once a data feed is connected."},
        {"label": "Energy trend", "value": "Demo data", "detail": "Use hourly, daily, weekly, or monthly views."},
    ]
    return render_template(
        "dashboard.html",
        active_page="dashboard",
        metrics=demo_metrics,
    )


@app.route("/load-forecasting", methods=["GET", "POST"])
def load_forecasting():
    result = None
    error = None

    if request.method == "POST":
        try:
            current_load = float(request.form.get("current_load", ""))
            annual_growth = float(request.form.get("annual_growth", ""))
            years = int(request.form.get("years", ""))

            if current_load < 0:
                raise ValueError("Current load must be zero or greater.")
            if not -100 <= annual_growth <= 100:
                raise ValueError("Growth rate must be between -100% and 100%.")
            if not 1 <= years <= 20:
                raise ValueError("Years must be between 1 and 20.")

            forecast = []
            for year in range(1, years + 1):
                projected = current_load * ((1 + annual_growth / 100) ** year)
                forecast.append(
                    {
                        "year": year,
                        "load": round(projected, 2),
                    }
                )

            result = {
                "current_load": current_load,
                "annual_growth": annual_growth,
                "years": years,
                "forecast": forecast,
            }
        except (TypeError, ValueError) as exc:
            error = str(exc) if str(exc) else "Please enter valid values."

    return render_template(
        "load_forecasting.html",
        active_page="load_forecasting",
        result=result,
        error=error,
    )


@app.route("/what-ifs", methods=["GET", "POST"])
def what_ifs():
    result = None
    error = None

    if request.method == "POST":
        try:
            annual_use = float(request.form.get("annual_use", ""))
            reduction = float(request.form.get("reduction", ""))

            if annual_use < 0:
                raise ValueError("Annual energy use must be zero or greater.")
            if not 0 <= reduction <= 100:
                raise ValueError("Reduction must be between 0% and 100%.")

            saved = annual_use * reduction / 100
            remaining = annual_use - saved

            result = {
                "annual_use": round(annual_use, 2),
                "reduction": round(reduction, 2),
                "saved": round(saved, 2),
                "remaining": round(remaining, 2),
            }
        except (TypeError, ValueError) as exc:
            error = str(exc) if str(exc) else "Please enter valid values."

    return render_template(
        "what_ifs.html",
        active_page="what_ifs",
        result=result,
        error=error,
    )


@app.route("/api/chat", methods=["POST"])
@limiter.limit("10 per minute")
def chat():
    if not OPENROUTER_API_KEY:
        return jsonify({"reply": "AVATAR AI isn't configured yet."}), 503

    data = request.get_json(silent=True) or {}
    history = data.get("messages", [])
    if not isinstance(history, list):
        history = []

    messages = []
    for m in history[-10:]:
        if (
            isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ):
            cap = 500 if m["role"] == "user" else 2000
            messages.append({"role": m["role"], "content": m["content"][:cap]})

    if not messages or messages[-1]["role"] != "user":
        return jsonify({"reply": "Please type a question."}), 400

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": MODEL,
                "max_tokens": 300,
                "messages": [{"role": "system", "content": get_system_prompt()}, *messages],
            },
            timeout=30,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": reply})
    except Exception:
        return jsonify({"reply": "Sorry, AVATAR AI is unavailable right now. Please try again shortly."}), 502


@app.route("/why-avatar")
def why_avatar():
    return render_template("why_avatar.html", active_page="why_avatar")


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")


@app.route("/contact")
def contact():
    return render_template(
        "simple_page.html",
        active_page="",
        page_title="Contact",
        page_intro="Add the project team's preferred email address, faculty contact, or department information here.",
    )


@app.route("/acknowledgements")
def acknowledgements():
    return render_template(
        "simple_page.html",
        active_page="",
        page_title="Acknowledgements",
        page_intro="Use this page to recognize project advisors, Villanova Facilities, faculty, students, and other collaborators.",
    )


@app.route("/sustainability-resources")
def sustainability_resources():
    return render_template(
        "simple_page.html",
        active_page="",
        page_title="Sustainability Resources",
        page_intro="Use this section for approved Villanova sustainability resources, reports, campus initiatives, and external references.",
    )


@app.route('/fun-facts')
def fun_facts():
    return render_template(
        'fun_facts.html',
        active_page='fun_facts'
    )

@app.route('/dorm-rankings')
def dorm_rankings():
    return render_template(
        'dorm_rankings.html',
        active_page='dorm_rankings'
    )

if __name__ == "__main__":
    app.run(debug=True)
