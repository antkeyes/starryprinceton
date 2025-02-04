import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
import google.oauth2.credentials  # Import this for OAuth credentials handling
from werkzeug.utils import secure_filename
import json
import requests
import csv

# -------------------------
# Define the Flask App
# -------------------------
app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"  # Replace with a secure key in production


@app.route('/testimonials')
def testimonials_page():
    # Path to your CSV file
    csv_file = os.path.join(app.root_path, 'testimonies.csv')

    # Column names as they appear in your CSV file
    name_col = "Firstname, Lastname (Can say Anonymous, but there isn't much point in hiding your name). "
    year_col = "(Anticipated) Year of graduation"
    q1_col = "Do you see a value in Princeton having a starry sky?"
    q2_col = "Did you experience light pollution on campus, and how did it disturb you?"

    csv_testimonies = []

    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                full_name = row.get(name_col, "").strip()
                grad_year = row.get(year_col, "").strip()
                q1_answer = row.get(q1_col, "").strip()
                q2_answer = row.get(q2_col, "").strip()

                # Convert name to "First LastInitial."
                split_name = full_name.split()
                if len(split_name) >= 2:
                    first_name = split_name[0]
                    last_initial = split_name[-1][0] + "."
                else:
                    first_name = full_name
                    last_initial = ""

                testimony_dict = {
                    "first_name": first_name,
                    "last_initial": last_initial,
                    "graduation_year": grad_year,
                    "q1_answer": q1_answer,
                    "q2_answer": q2_answer
                }
                csv_testimonies.append(testimony_dict)
    except Exception as e:
        print("Error reading CSV file:", e)

    return render_template("testimonials.html", csv_testimonies=csv_testimonies)


@app.route('/update_description', methods=['POST'])
def update_description():
    media_item_id = request.form.get('media_item_id')
    description = request.form.get('description')

    if not media_item_id:
        flash("Media item ID is missing.", "error")
        return redirect(url_for('index'))

    # Look up the record in the database
    record = MediaDescription.query.filter_by(media_item_id=media_item_id).first()

    if record:
        # Update existing record
        record.description = description
    else:
        # Create a new record if one doesn't exist
        record = MediaDescription(media_item_id=media_item_id, description=description)
        db.session.add(record)

    db.session.commit()
    flash("Description updated successfully!", "success")
    return redirect(url_for('index'))


@app.template_filter('split')
def split_filter(s, sep=None):
    # Make sure s is a string; you might want to add error checking
    return s.split(sep)


# Configure the database URI (here we use a local SQLite database)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///descriptions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Define a model to store descriptions for each media item.
class MediaDescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    media_item_id = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

# Create the database tables if they don't exist
with app.app_context():
    db.create_all()

# -------------------------
# Helper Functions
# -------------------------
def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

#test helper function to list albums
def list_albums():
    if 'credentials' not in session:
        print("No OAuth credentials found. Please authorize first.")
        return []
    
    credentials = google.oauth2.credentials.Credentials(**session['credentials'])
    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except Exception as e:
            print("Error refreshing credentials:", e)
            return []
    
    access_token = credentials.token

    url = 'https://photoslibrary.googleapis.com/v1/albums'
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('albums', [])
    else:
        print("Error listing albums:", response.text)
        return []


# -------------------------
# OAuth Routes
# -------------------------

#test to display the albums
@app.route("/albums")
def albums():
    albums_list = list_albums()
    # For simplicity, return the raw JSON (in a real app, you'd format this nicely)
    return {"albums": albums_list}


@app.route('/authorize')
def authorize():
    # Create an OAuth flow instance from the client secrets file
    flow = Flow.from_client_secrets_file(
        'client_secret.json',  # Ensure your file is named client_secret.json or update this
        scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    
    # Generate the authorization URL where the user will be sent
    authorization_url, state = flow.authorization_url(
         access_type='offline',
         include_granted_scopes='true'
    )
    # Store the state in the session for later use in the callback
    session['state'] = state
    return redirect(authorization_url)


@app.route('/oauth2callback')
def oauth2callback():
    # Retrieve the state stored in the session
    state = session['state']
    
    # Recreate the flow instance with the same state and redirect URI
    flow = Flow.from_client_secrets_file(
         'client_secret.json',
         scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
         state=state,
         redirect_uri=url_for('oauth2callback', _external=True)
    )
    
    # Exchange the authorization code in the callback URL for an access token
    flow.fetch_token(authorization_response=request.url)
    
    # Save the credentials in the session
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    
    # Redirect back to your home page (or another page if you prefer)
    return redirect(url_for('index'))

# -------------------------
# API Helper Function Using OAuth
# -------------------------
def fetch_album_items(album_id):
    # Check if OAuth credentials are available in the session
    if 'credentials' not in session:
        print("No OAuth credentials found. Please authorize first by visiting /authorize")
        return []
    
    # Create credentials object from the stored session data
    credentials = google.oauth2.credentials.Credentials(**session['credentials'])
    
    # Refresh credentials if necessary
    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except Exception as e:
            print("Error refreshing credentials:", e)
            return []
    
    access_token = credentials.token

    url = 'https://photoslibrary.googleapis.com/v1/mediaItems:search'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    data = {
        'album_id': "AEVtP4Aw64PA0nj6cfP-wofknQrsFqnWkKON_UyptQpPTAr-jzVKONdocx7BANAKmH6vRQIA6cH9",
        'pageSize': 50  # Adjust this as needed
    }

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        items = response.json().get('mediaItems', [])
        return items
    else:
        print('Error fetching album items:', response.text)
        return []

# -------------------------
# Configuration and Sample Data
# -------------------------
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "mp4", "mov", "avi"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload size (example)

# Sample Data
testimonials = [
    {"name": "John D.", "quote": "The bright lights outside the dorm keep me up all night."},
    {"name": "Anonymous", "quote": "I never realized how much energy is wasted on unnecessary lighting."},
]
research_articles = [
    {
        "title": "Study: Effects of Light Pollution on Wildlife",
        "summary": "This research explores how nocturnal species are impacted by excessive artificial lighting.",
        "link": "https://example.com/research1",
    },
    {
        "title": "Local News: Community Takes Action",
        "summary": "A neighborhood reduces energy costs by dimming street lamps after midnight.",
        "link": "https://example.com/news2",
    },
]
curated_media = [
    {
        "type": "image",
        "src": "https://via.placeholder.com/300x200?text=Curated+Campus+Photo+1",
        "description": "Bright walkway lights near the library at 2 AM.",
    },
    {
        "type": "video",
        "src": "https://www.w3schools.com/html/mov_bbb.mp4",
        "description": "Short clip showing overhead lights in the parking lot.",
    },
]
user_submissions = []  # In-memory list for demo; use a DB in production

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# -------------------------
# Main Routes
# -------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    global user_submissions

    # Handle file uploads (your existing POST handling code)
    if request.method == "POST":
        # ... your file upload code here ...
        return redirect(url_for("index"))

    # If OAuth credentials exist, fetch the album items
    google_photos_media = []
    if 'credentials' in session:
        # IMPORTANT: Use the correct album ID (from your /albums output)
        album_id = "AEVtP4Aw64PA0nj6cfP-wofknQrsFqnWkKON_UyptQpPTAr-jzVKONdocx7BANAKmH6vRQIA6cH9"
        google_photos_media = fetch_album_items(album_id)

    # Build a dictionary of custom descriptions for the fetched media items.
    custom_descriptions = {}
    if google_photos_media:
        for item in google_photos_media:
            record = MediaDescription.query.filter_by(media_item_id=item['id']).first()
            if record:
                custom_descriptions[item['id']] = record.description

    # ---- Parse your CSV for testimonials ----
    import csv
    csv_file = os.path.join(app.root_path, 'testimonies.csv')

    # These must match the EXACT column headers in your CSV
    name_col = "Firstname, Lastname (Can say Anonymous, but there isn't much point in hiding your name). "
    year_col = "(Anticipated) Year of graduation"
    q1_col = "Do you see a value in Princeton having a starry sky?"
    q2_col = "Did you experience light pollution on campus, and how did it disturb you?"

    csv_testimonies = []  # We'll store processed data for each row here

    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Extract each field
                full_name = row.get(name_col, "").strip()
                grad_year = row.get(year_col, "").strip()
                q1_answer = row.get(q1_col, "").strip()
                q2_answer = row.get(q2_col, "").strip()

                # Convert "Adriana Gaitan" -> "Adriana G."
                split_name = full_name.split()
                if len(split_name) >= 2:
                    first_name = split_name[0]
                    last_initial = split_name[-1][0] + "."  # first letter of last word + "."
                else:
                    # If only one part, treat it as the entire name
                    first_name = full_name
                    last_initial = ""

                # Build a dict for the template
                testimony_dict = {
                    "first_name": first_name,
                    "last_initial": last_initial,
                    "graduation_year": grad_year,
                    "q1_answer": q1_answer,
                    "q2_answer": q2_answer
                }
                csv_testimonies.append(testimony_dict)
    except Exception as e:
        print("Error reading CSV file:", e)
        csv_testimonies = []  # fallback if error

    # ------------------------------------------------
    # Now render the template, passing 'csv_testimonies'
    return render_template(
        "index.html",
        testimonials=testimonials,  # existing sample data if you still need it
        research_articles=research_articles,
        curated_media=curated_media,
        user_submissions=user_submissions,
        google_photos_media=google_photos_media,
        custom_descriptions=custom_descriptions,
        csv_testimonies=csv_testimonies  # pass new list
    )

# -------------------------
# Run the App
# -------------------------
if __name__ == "__main__":
    # # Test the Google Photos API call (ensure you've authorized first via /authorize)
    # test_album_id = "AF1QipNLTaqMMG4qZRpNRpcwhiRrGI6--CGP2jhS4oRR"
    # items = fetch_album_items(test_album_id)
    # print("Fetched items:", items)

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    app.run(debug=True, host="0.0.0.0", port=5003)
