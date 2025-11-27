from flask import Flask, request, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import bcrypt
import mysql.connector
from datetime import datetime
from flask_cors import CORS
import requests
from openai import OpenAI
from flask_jwt_extended.exceptions import NoAuthorizationError


app = Flask(__name__)

# ✅ MORE PERMISSIVE CORS
CORS(app, 
     resources={r"/*": {"origins": "*"}}, 
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "OPTIONS"])

app.config["JWT_SECRET_KEY"] = "super-secret-key"
jwt = JWTManager(app)

#  Connect to MySQL (XAMPP)
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="marinell",
        password="marinellendaya",
        database="ai_health_db"
    )

#  Register a new user
@app.route("/register", methods=["POST"])
def register():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    data = request.json
    hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt())
    cursor.execute("INSERT INTO accounts (fullname, age, email, password) VALUES (%s, %s, %s, %s)",
                   (data["fullname"], data["age"], data["email"], hashed))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"message": "User registered"}), 201

#  Login and return token
@app.route("/login", methods=["POST"])
def login():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    data = request.json
    cursor.execute("SELECT * FROM accounts WHERE email = %s", (data["email"],))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    if user and bcrypt.checkpw(data["password"].encode(), user["password"].encode()):
        token = create_access_token(identity=str(user["id"]))
        return jsonify({"access_token": token, "user_id": user["id"]})
    return jsonify({"error": "Invalid credentials"}), 401

# Receive sensor data from ESP32 WITH authentication
@app.route("/sensor", methods=["POST"])
@jwt_required()
def receive_sensor_data():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    user_id = get_jwt_identity()
    data = request.json
    cursor.execute("INSERT INTO sensor_readings (user_id, spo2, heart_rate, timestamp) VALUES (%s, %s, %s, %s)",
                   (user_id, data["spo2"], data["heart_rate"], datetime.now()))
    db.commit()
    reading_id = cursor.lastrowid
    cursor.execute("INSERT INTO health_records (user_id, reading_id) VALUES (%s, %s)",
                   (user_id, reading_id))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"message": "Reading stored"}), 200

# Fetch latest health logs
@app.route("/healthlogs", methods=["GET"])
@jwt_required()
def get_logs():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    user_id = get_jwt_identity()
    cursor.execute("""
        SELECT sr.spo2, sr.heart_rate, sr.timestamp
        FROM sensor_readings sr
        WHERE sr.user_id = %s
        ORDER BY sr.timestamp DESC
        LIMIT 20
    """, (user_id,))
    logs = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(logs)

# ESP32 endpoint WITHOUT authentication (for testing)
@app.route("/sensor-readings", methods=["GET", "POST", "OPTIONS"])
def sensor_readings():
    if request.method == "OPTIONS":
        return jsonify({"message": "CORS preflight OK"}), 200

    try:
        data = request.get_json()
        print("✅ Received data from ESP32:", data)
        
        # Save to database - using user_id from ESP32 payload
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # Make sure user_id is provided
        user_id = data.get("user_id", 1)  # Default to 1 if not provided
        heart_rate = data.get("heart_rate", 0)
        spo2 = data.get("spo2", 0)
        
        cursor.execute(
            "INSERT INTO sensor_readings (user_id, heart_rate, spo2, timestamp) VALUES (%s, %s, %s, %s)",
            (user_id, heart_rate, spo2, datetime.now())
        )
        db.commit()
        reading_id = cursor.lastrowid
        
        cursor.close()
        db.close()
        
        response = jsonify({
            "message": "Data received and stored",
            "reading_id": reading_id,
            "data": data
        })
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response, 200
        
    except Exception as e:
        print("❌ Error storing sensor data:", str(e))
        response = jsonify({"error": str(e)})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response, 500

OPENAI_API_KEY = "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"

client = OpenAI(api_key=OPENAI_API_KEY)

@app.route("/ai-assistant", methods=["POST"])
def ai_assistant():
    try:
        data = request.json
        messages = data.get("messages", [])

        # Convert messages[] into a single string (for new API)
        user_input = ""
        for msg in messages:
            if msg["role"] == "user":
                user_input += msg["content"] + "\n"

        # --- NEW API FORMAT (OpenAI SDK v3+)
        response = client.responses.create(
            model="gpt-4o-mini",
            input=user_input,
        )

        ai_message = response.output[0].content[0].text

        return jsonify({"content": [{"text": ai_message}]})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/user-info", methods=["GET"])
@jwt_required()
def user_info():
    user_id = get_jwt_identity()

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT fullname FROM accounts WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    cursor.close()
    db.close()

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "fullname": user["fullname"]
    })

# Better JWT error responses (add after jwt = JWTManager(app))
@jwt.unauthorized_loader
def unauthorized_response(callback):
    # No Authorization header present
    return jsonify({"error": "Authorization header missing"}), 401

@jwt.invalid_token_loader
def invalid_token_callback(reason):
    return jsonify({"error": "Invalid token", "details": reason}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({"error": "Token expired"}), 401

@jwt.revoked_token_loader
def revoked_token_callback(jwt_header, jwt_payload):
    return jsonify({"error": "Token has been revoked"}), 401

@app.errorhandler(NoAuthorizationError)
def handle_auth_error(e):
    print("AUTH ERROR:", str(e))
    return jsonify({"error": str(e)}), 401

### new


#  Run the app
if __name__ == "__main__":
    print("🚀 Starting Flask server...")
    print("📡 Accessible at:")
    print("   - http://192.168.100.13:5000")
    print("   - http://127.0.0.1:5000")
    print("="*50)
    app.run(host="0.0.0.0", port=5000, debug=True)