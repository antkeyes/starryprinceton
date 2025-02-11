import os
from datetime import timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)  # Use INFO or WARNING for less verbosity in production
logger = logging.getLogger(__name__)

# Check the FLASK_ENV variable. If it's not set to "production", allow insecure transport.
if os.environ.get("FLASK_ENV") != "production":
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from dateutil import parser
import google.oauth2.credentials
from werkzeug.utils import secure_filename
import json
import requests
import csv

# -------------------------
# Define the Flask App
# -------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "development_secret_key")
app.permanent_session_lifetime = timedelta(days=30)

# -------------------------
# Configure the Database
# -------------------------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////nfs/hatops/ar0/starryprinceton/instance/descriptions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# -------------------------
# Models
# -------------------------
class MediaDescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    media_item_id = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    user_name = db.Column(db.String(100))
    latitude = db.Column(db.String(50))
    longitude = db.Column(db.String(50))

# New model for persisting OAuth credentials
class OAuthCredentials(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), unique=True, nullable=False)  # For a single user, we use a default id.
    token = db.Column(db.String(512))
    refresh_token = db.Column(db.String(512))
    token_uri = db.Column(db.String(512))
    client_id = db.Column(db.String(512))
    client_secret = db.Column(db.String(512))
    scopes = db.Column(db.String(1024))  # Stored as a comma-separated list
    expiry = db.Column(db.DateTime, nullable=True)

    def to_credentials(self):
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=self.token,
            refresh_token=self.refresh_token,
            token_uri=self.token_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=self.scopes.split(',') if self.scopes else []
        )
        if self.expiry:
            creds.expiry = self.expiry
        return creds

    def update_from_credentials(self, credentials):
        self.token = credentials.token
        # Only update the refresh token if a new one is provided.
        if credentials.refresh_token:
            self.refresh_token = credentials.refresh_token
        self.token_uri = credentials.token_uri
        self.client_id = credentials.client_id
        self.client_secret = credentials.client_secret
        self.scopes = ','.join(credentials.scopes) if credentials.scopes else ''
        self.expiry = credentials.expiry

# Create the database tables if they don't exist.
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
        'scopes': credentials.scopes,
        'expiry': credentials.expiry.isoformat() if credentials.expiry else None
    }

def get_user_credentials():
    # Always use a default user id for simplicity.
    user_id = "default_user"  # Alternatively: session.get('user_id', 'default_user')
    creds_record = OAuthCredentials.query.filter_by(user_id=user_id).first()
    if creds_record is None:
        logger.error("No credentials record found for user_id: %s", user_id)
        return None
    credentials = creds_record.to_credentials()

    if credentials.expired or not credentials.valid:
        try:
            credentials.refresh(Request())
            creds_record.update_from_credentials(credentials)
            db.session.commit()
        except Exception:
            return None

    return credentials

def list_albums():
    credentials = get_user_credentials()
    if credentials is None:
        logger.error("No OAuth credentials found. Please authorize first.")
        return []
    access_token = credentials.token
    url = 'https://photoslibrary.googleapis.com/v1/albums'
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('albums', [])
    else:
        logger.error("Error listing albums: %s", response.text)
        return []

def fetch_album_items(album_id):
    credentials = get_user_credentials()
    if credentials is None:
        logger.error("No OAuth credentials found. Please authorize first by visiting /authorize")
        return []
    access_token = credentials.token
    url = 'https://photoslibrary.googleapis.com/v1/mediaItems:search'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    data = {'album_id': album_id, 'pageSize': 50}
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json().get('mediaItems', [])
    else:
        logger.error("Error fetching album items: %s", response.text)
        return []

# -------------------------
# Template Filter
# -------------------------
@app.template_filter('split')
def split_filter(s, sep=None):
    return s.split(sep)

# -------------------------
# OAuth Routes
# -------------------------
@app.route('/authorize')
def authorize():
    flow = Flow.from_client_secrets_file(
        'client_secret.json',
        scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    authorization_url, state = flow.authorization_url(
         access_type='offline',
         include_granted_scopes='true',
         prompt='consent'
    )
    session['state'] = state
    session.permanent = True  # Mark the session as permanent so it lasts longer.
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Log the session contents
    logger.debug("Session contents at /oauth2callback: %s", dict(session))
    
    state = session.get('state')
    if not state:
        flash("Session expired or state missing. Please authorize the app again.", "error")
        return redirect(url_for('authorize'))
    
    flow = Flow.from_client_secrets_file(
         'client_secret.json',
         scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
         state=state,
         redirect_uri=url_for('oauth2callback', _external=True)
    )
    
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as e:
        flash("Failed to fetch token: " + str(e), "error")
        logger.error("Failed to fetch token: %s", e, exc_info=True)
        return redirect(url_for('authorize'))
    
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    
    # Persist credentials in the database for the default user.
    user_id = "default_user"
    creds_record = OAuthCredentials.query.filter_by(user_id=user_id).first()
    if not creds_record:
        creds_record = OAuthCredentials(user_id=user_id)
        db.session.add(creds_record)
    creds_record.update_from_credentials(credentials)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/testimonials')
def testimonials_page():
    csv_file = os.path.join(app.root_path, 'testimonies.csv')
    name_col = "Firstname, Lastname (Can say Anonymous, but there isn't much point in hiding your name). "
    year_col = "(Anticipated) Year of graduation"
    q1_col = "Do you see a value in Princeton having a starry sky?  Describe your relevant experience."
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
                grading_gb = row.get("Grading GB", "").strip()
                grading_ak = row.get("Grading AK", "").strip()
                split_name = full_name.split()
                if len(split_name) >= 2:
                    first_name = split_name[0]
                    last_initial = split_name[-1][0] + "."
                else:
                    first_name = full_name
                    last_initial = ""
                csv_testimonies.append({
                    "first_name": first_name,
                    "last_initial": last_initial,
                    "graduation_year": grad_year,
                    "q1_answer": q1_answer,
                    "q2_answer": q2_answer,
                    "grading_gb": grading_gb,
                    "grading_ak": grading_ak
                })
    except Exception as e:
        logger.error("Error reading CSV file: %s", e, exc_info=True)
    return render_template("testimonials.html", csv_testimonies=csv_testimonies)

# -------------------------
# Route for Updating Description
# -------------------------
@app.route('/update_description', methods=['POST'])
def update_description():
    media_item_id = request.form.get('media_item_id')
    description = request.form.get('description')
    if not media_item_id:
        flash("Media item ID is missing.", "error")
        return redirect(url_for('index'))
    record = MediaDescription.query.filter_by(media_item_id=media_item_id).first()
    if record:
        record.description = description
    else:
        record = MediaDescription(media_item_id=media_item_id, description=description)
        db.session.add(record)
    db.session.commit()
    flash("Description updated successfully!", "success")
    return redirect(url_for('index'))

# -------------------------
# Configuration for File Uploads and Sample Data
# -------------------------
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "mp4", "mov", "avi"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload size

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

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# -------------------------
# Main Route (Index)
# -------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file = request.files.get("media_file")
        description = request.form.get("description")
        user_name = request.form.get("user_name")
        latitude = request.form.get("latitude")
        longitude = request.form.get("longitude")
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            new_submission = Submission(
                filename=filename,
                description=description,
                user_name=user_name,
                latitude=latitude,
                longitude=longitude
            )
            db.session.add(new_submission)
            db.session.commit()
            flash("Your submission has been uploaded!", "success")
        else:
            flash("Invalid file type.", "error")
        return redirect(url_for("index"))
    
    # GET request handling
    google_photos_media = []
    if get_user_credentials() is not None:
        album_id = "AEVtP4Aw64PA0nj6cfP-wofknQrsFqnWkKON_UyptQpPTAr-jzVKONdocx7BANAKmH6vRQIA6cH9"
        google_photos_media = fetch_album_items(album_id)
    
    custom_descriptions = {}
    if google_photos_media:
        for item in google_photos_media:
            record = MediaDescription.query.filter_by(media_item_id=item['id']).first()
            if record:
                custom_descriptions[item['id']] = record.description
    
    submissions = Submission.query.all()
    
    csv_file = os.path.join(app.root_path, 'testimonies.csv')
    name_col = "Firstname, Lastname (Can say Anonymous, but there isn't much point in hiding your name). "
    year_col = "(Anticipated) Year of graduation"
    q1_col = "Do you see a value in Princeton having a starry sky?  Describe your relevant experience."
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
                grading_gb = row.get("Grading GB", "").strip()
                grading_ak = row.get("Grading AK", "").strip()
                split_name = full_name.split()
                if len(split_name) >= 2:
                    first_name = split_name[0]
                    last_initial = split_name[-1][0] + "."
                else:
                    first_name = full_name
                    last_initial = ""
                csv_testimonies.append({
                    "first_name": first_name,
                    "last_initial": last_initial,
                    "graduation_year": grad_year,
                    "q1_answer": q1_answer,
                    "q2_answer": q2_answer,
                    "grading_gb": grading_gb,
                    "grading_ak": grading_ak
                })
    except Exception as e:
        logger.error("Error reading CSV file: %s", e, exc_info=True)
    
    return render_template(
        "index.html",
        testimonials=testimonials,
        research_articles=research_articles,
        curated_media=curated_media,
        user_submissions=submissions,
        google_photos_media=google_photos_media,
        custom_descriptions=custom_descriptions,
        csv_testimonies=csv_testimonies
    )

# -------------------------
# Run the App (for local testing)
# -------------------------
if __name__ == "__main__":
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.run(debug=True, host="0.0.0.0", port=5003)
