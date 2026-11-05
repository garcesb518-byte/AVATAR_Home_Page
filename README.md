# AVATAR Home Page

AVATAR stands for **Advancing Villanova to Energy Autonomy and Resiliency**. This repository contains a Flask-based starter website for a student-led Villanova University energy transparency and resiliency project.

The site is designed to keep the homepage simple while sending users to focused pages for:

- Dashboard
- Load Forecasting
- What Ifs
- AVATAR AI
- Why AVATAR?
- About

The current forecasting, scenario, dashboard, and AVATAR AI content is demonstration functionality. It is intended to be replaced or extended with approved Villanova data and the team's production models.

## Project structure

```text
AVATAR_Home_Page/
├── app.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── home.html
│   ├── dashboard.html
│   ├── load_forecasting.html
│   ├── what_ifs.html
│   ├── avatar_ai.html
│   ├── why_avatar.html
│   ├── about.html
│   └── simple_page.html
└── static/
    ├── styles.css
    └── script.js
```

## Run locally

1. Clone the repository.
2. Open a terminal in the project folder.
3. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

4. Start the Flask server:

```bash
python app.py
```

5. Open `http://127.0.0.1:5000` in your browser.

## Homepage image

The homepage references an official Villanova University campus image hosted on Villanova's website. For a production deployment, the project team should use a Villanova-approved image asset and host it locally in `static/images/` if appropriate.

## Forecast-driven simulated load

Run `python server.py` and open `http://127.0.0.1:8000/load-forecasting` for the
integrated current AVATAR page, forecast API and virtual load controller.
This entry point is separate from the legacy `app.py` starter described above.
See [the setup, rules and forecast contract](docs/SIMULATED_LOAD_CONTROL.md).
The public GitHub Pages site includes the demonstration interface and setup links.
Run the Python server locally to use the controls; GitHub Pages does not execute Python.
No physical relay is connected.

## Bartley timing proxy

The temporary campus load curve uses normalized weekday/weekend timing shapes
derived from Bartley Hall's 15-minute SEL present-demand export. Bartley's raw
kW readings are not committed to this public repository and do not set the
campus magnitude. The forecast renormalizes the selected timing shape to the
104 MWh/day campus baseline.

To reproduce the dimensionless shapes from an approved local copy of the SEL
export, run:

```bash
python tools/derive_bartley_proxy_shape.py "Bartley SEL Present Demand.csv"
```
