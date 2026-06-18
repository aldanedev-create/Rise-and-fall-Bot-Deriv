from dotenv import load_dotenv
from flask import Flask

from app.extensions import db, socketio


def create_app() -> Flask:
    load_dotenv()

    from config import Config

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    db.init_app(app)
    socketio.init_app(app)

    from app.routes import bp as routes_bp
    from app.sockets import register_socket_events

    app.register_blueprint(routes_bp)
    register_socket_events(socketio)

    with app.app_context():
        db.create_all()

    from app.services.scanner import DerivSignalScanner

    app.scanner = DerivSignalScanner(app, socketio)
    return app
