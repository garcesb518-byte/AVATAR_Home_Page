# AVATAR Home Page

AVATAR stands for **Advancing Villanova to Energy Autonomy and Resiliency**. This repository contains a Flask-based starter website for a student-led Villanova University energy transparency and resiliency project.

The site is designed to keep the homepage simple while sending users to focused pages for:

- Dashboard
- Load Forecasting
- What Ifs
- AVATAR AI
- Why AVATAR?
- About

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
