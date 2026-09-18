from flask import Flask, render_template, request

app = Flask(__name__)

NAV_ITEMS = [
    ("dashboard", "Dashboard"),
    ("load_forecasting", "Load Forecasting"),
    ("what_ifs", "What Ifs"),
    ("avatar_ai", "AVATAR AI"),
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


@app.route("/avatar-ai", methods=["GET", "POST"])
def avatar_ai():
    answer = None
    question = ""

    if request.method == "POST":
        question = request.form.get("question", "").strip()
        q = question.lower()

        if not question:
            answer = "Ask a question about campus energy, forecasting, sustainability, or resiliency."
        elif "forecast" in q or "future load" in q:
            answer = (
                "The Load Forecasting section is designed to estimate how campus demand "
                "could change over time. Once AVATAR is connected to real Villanova data, "
                "the model can use historical load, weather, occupancy, and calendar patterns."
            )
        elif "what if" in q or "scenario" in q:
            answer = (
                "The What Ifs section is where AVATAR can compare scenarios such as lower "
                "energy use, solar generation, storage, or other campus changes."
            )
        elif "avatar" in q:
            answer = (
                "AVATAR stands for Advancing Villanova to Energy Autonomy and Resiliency. "
                "The project is meant to make campus energy easier to understand and support "
                "better decisions about sustainability and resiliency."
            )
        else:
            answer = (
                "This is a starter AVATAR AI response. The page and interaction are working, "
                "but a production version should connect this form to your chosen AI model "
                "and Villanova-approved energy data sources."
            )

    return render_template(
        "avatar_ai.html",
        active_page="avatar_ai",
        answer=answer,
        question=question,
    )


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
