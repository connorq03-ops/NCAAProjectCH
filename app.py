from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from kenpom_client import KenpomClient

app = Flask(__name__, static_folder='static')
CORS(app)

API_KEY = "c778f2c8af554d059b4c9413fc7ea44a7d45da806a19985421c4dd311816ef6e"
client = KenpomClient(api_key=API_KEY)


@app.route('/api/ratings', methods=['GET'])
def get_ratings():
    """Get team ratings."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        data = client.get_ratings(year=year, team_id=team_id, conference=conference)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/archive', methods=['GET'])
def get_archive():
    """Get historical ratings data."""
    date = request.args.get('date')
    year = request.args.get('year', type=int)
    preseason = request.args.get('preseason', '').lower() == 'true'
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        data = client.get_archive(date=date, year=year, preseason=preseason, 
                                  team_id=team_id, conference=conference)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/four-factors', methods=['GET'])
def get_four_factors():
    """Get four factors statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        data = client.get_four_factors(year=year, team_id=team_id, 
                                       conference=conference, conf_only=conf_only)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pointdist', methods=['GET'])
def get_point_distribution():
    """Get point distribution statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        data = client.get_point_distribution(year=year, team_id=team_id,
                                             conference=conference, conf_only=conf_only)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/height', methods=['GET'])
def get_height():
    """Get team height statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    
    try:
        data = client.get_height(year=year, team_id=team_id, conference=conference)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/misc-stats', methods=['GET'])
def get_misc_stats():
    """Get miscellaneous statistics."""
    year = request.args.get('year', type=int)
    team_id = request.args.get('team_id', type=int)
    conference = request.args.get('conference')
    conf_only = request.args.get('conf_only', '').lower() == 'true'
    
    try:
        data = client.get_misc_stats(year=year, team_id=team_id,
                                     conference=conference, conf_only=conf_only)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fanmatch', methods=['GET'])
def get_fanmatch():
    """Get game predictions for a specific date."""
    date = request.args.get('date')
    
    if not date:
        return jsonify({'error': 'Date parameter is required'}), 400
    
    try:
        data = client.get_fanmatch(date=date)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conf-ratings', methods=['GET'])
def get_conference_ratings():
    """Get conference ratings."""
    year = request.args.get('year', type=int)
    conference = request.args.get('conference')
    
    try:
        data = client.get_conference_ratings(year=year, conference=conference)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/teams', methods=['GET'])
def get_teams():
    """Get list of teams."""
    year = request.args.get('year', type=int)
    conference = request.args.get('conference')
    
    try:
        data = client.get_teams(year=year, conference=conference)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conferences', methods=['GET'])
def get_conferences():
    """Get list of conferences."""
    year = request.args.get('year', type=int)
    
    try:
        data = client.get_conferences(year=year)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    """Serve the main HTML page."""
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001)
