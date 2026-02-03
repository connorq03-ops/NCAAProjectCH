# NCAA - Kenpom College Basketball Analytics

A web application for exploring Kenpom college basketball analytics data with an interactive UI.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## Features

- **Team Ratings** - View AdjEM, AdjO, AdjD, tempo, luck, and strength of schedule
- **Four Factors** - eFG%, TO%, OR%, FT Rate for offense and defense
- **Height & Experience** - Team height stats, experience, bench depth, continuity
- **Scoring Distribution** - Point distribution from FT, 2PT, and 3PT
- **Shooting Stats** - Detailed shooting percentages and defensive metrics
- **Conference Rankings** - Conference strength ratings
- **Game Predictions** - Fanmatch predictions with win probabilities
- **Historical Archive** - View ratings from any date in the season
- **Sortable Tables** - Click any column header to sort
- **Team Detail Modal** - Click any team to see all their stats

## Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Set your Kenpom API key** (optional - already configured):
```bash
export KENPOM_API_KEY="your_api_key_here"
```

3. **Run the application:**
```bash
python3 app.py
```

4. **Open in browser:**
```
http://localhost:5001
```

## Project Structure

```
NCAA/
├── app.py                 # Flask backend server
├── kenpom_client.py       # Kenpom API client
├── static/
│   └── index.html         # Frontend UI
├── requirements.txt       # Python dependencies
└── README.md
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/ratings` | Team ratings (year, team_id, conference) |
| `/api/four-factors` | Four factors stats |
| `/api/height` | Height and experience data |
| `/api/pointdist` | Point distribution |
| `/api/misc-stats` | Shooting and misc stats |
| `/api/conf-ratings` | Conference rankings |
| `/api/fanmatch` | Game predictions (date) |
| `/api/archive` | Historical ratings (date) |
| `/api/teams` | Team list |
| `/api/conferences` | Conference list |

## Technologies

- **Backend:** Python, Flask, Flask-CORS
- **Frontend:** HTML, JavaScript, TailwindCSS
- **API:** Kenpom College Basketball API

## License

MIT
