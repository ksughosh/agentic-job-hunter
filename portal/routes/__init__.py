"""Register all blueprints on the Flask app."""

from portal.routes.pages import bp as pages_bp
from portal.routes.users import bp as users_bp
from portal.routes.pipeline import bp as pipeline_bp
from portal.routes.documents import bp as documents_bp
from portal.routes.settings import bp as settings_bp
from portal.routes.sources import bp as sources_bp
from portal.routes.search_profiles import bp as search_profiles_bp


def register_all(app):
    app.register_blueprint(pages_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(pipeline_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(search_profiles_bp)
