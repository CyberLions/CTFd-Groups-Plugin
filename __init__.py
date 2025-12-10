from flask import request, jsonify, abort
from CTFd.utils.modes import TEAMS_MODE
from CTFd.models import Users, Teams
from CTFd.utils.user import get_current_user
from CTFd import utils

def load(app):

    # per-bracket limits (None = unlimited)
    BRACKET_LIMITS = {
        "PSU": 4,
        "Academic": None,
        "Open": None,
    }

    def debug(msg):
        print(f"[TEAM_LIMIT_PLUGIN] {msg}", flush=True)

    # ---- PSU EMAIL CHECK ----
    def email_is_psu(user):
        try:
            return user and user.email and user.email.lower().endswith("@psu.edu")
        except Exception:
            return False

    def get_team_bracket_from_team_obj(team):
        if team is None:
            return None

        for attr in ("bracket", "bracket_id", "scoreboard_bracket", "scoreboard_bracket_id"):
            try:
                value = getattr(team, attr, None)
                if value:
                    name = getattr(value, "name", None)
                    if name:
                        return str(name)
                    return str(value)
            except Exception:
                continue

        try:
            extra = getattr(team, "extra", None)
            if extra and isinstance(extra, dict):
                for key in ("bracket", "scoreboard_bracket", "bracket_name"):
                    if extra.get(key):
                        return str(extra.get(key))
        except Exception:
            pass

        return None

    def find_team_by_form(form):
        team = None
        for key in ("team_id", "teamid", "team"):
            if key in form and form.get(key):
                val = form.get(key)
                if val.isdigit():
                    team = Teams.query.filter_by(id=int(val)).first()
                    if team:
                        return team
                team = Teams.query.filter_by(name=val).first()
                if team:
                    return team

        if "name" in form and form.get("name"):
            team = Teams.query.filter_by(name=form.get("name")).first()
            if team:
                return team

        return None


    @app.before_request
    def team_limit_check():
        endpoint = request.endpoint
        debug(f"Incoming request to endpoint: {endpoint}")

        if not utils.get_config("user_mode") == TEAMS_MODE:
            debug("Not in TEAMS mode, skipping validation.")
            return

        if endpoint not in ("teams.new", "teams.join", "api_teams_join"):
            debug(f"Endpoint {endpoint} does not require validation — skipping.")
            return

        form = request.form or {}
        json_payload = request.get_json(silent=True) or {}
        debug(f"Form keys: {list(form.keys())}; JSON keys: {list(json_payload.keys())}")

        user = get_current_user()
        user_display = getattr(user, "name", None) or getattr(user, "username", None) or "unknown"
        debug(f"Current user: {user_display}")


        # ------------------------------------------
        # TEAM CREATION: PSU EMAIL ENFORCEMENT
        # ------------------------------------------
        if endpoint == "teams.new" and request.method == "POST":

            bracket_val = None
            for key in ("bracket", "bracket_id", "scoreboard_bracket", "scoreboard_bracket_id"):
                if key in form and form.get(key):
                    bracket_val = form.get(key)
                    break

            debug(f"Team creation requested by '{user_display}'. Bracket choice (form) = {bracket_val}")

            # ---- ENFORCE PSU EMAIL IF BRACKET = PSU ----
            if bracket_val and str(bracket_val).strip() == "PSU":
                if not email_is_psu(user):
                    debug("DENY team creation: PSU bracket requires @psu.edu email")
                    abort(400, description="PSU teams require members with @psu.edu email addresses.")

            return


        # ------------------------------------------
        # TEAM JOIN: LIMITS + PSU EMAIL ENFORCEMENT
        # ------------------------------------------
        if endpoint in ("teams.join", "api_teams_join") and request.method in ("POST", "PUT"):

            team = None
            if json_payload.get("team_id"):
                try:
                    team = Teams.query.filter_by(id=int(json_payload.get("team_id"))).first()
                except Exception:
                    team = None

            if not team:
                team = find_team_by_form(form)

            if not team:
                debug("Could not determine the target team from the request.")
                return

            debug(f"Target team resolved: id={team.id} name='{team.name}'")

            bracket = get_team_bracket_from_team_obj(team)
            debug(f"Bracket (from team object) resolved to: {bracket}")

            if not bracket:
                for key in ("bracket", "bracket_id", "scoreboard_bracket"):
                    if key in form and form.get(key):
                        bracket = form.get(key)
                        debug(f"Bracket (from join form) = {bracket}")
                        break

            if not bracket:
                debug("No bracket found for team; skipping per-bracket checks.")
                return

            bracket = str(bracket).strip()
            limit = BRACKET_LIMITS.get(bracket)
            debug(f"Bracket '{bracket}' limit = {limit}")

            # ---- PSU EMAIL CHECK FOR JOIN ----
            if bracket == "PSU":
                if not email_is_psu(user):
                    debug(f"DENY join: PSU bracket requires @psu.edu email")
                    return jsonify({
                        "success": False,
                        "message": "Only @psu.edu emails may join PSU teams."
                    }), 400

            # ---- MEMBER COUNT LIMIT CHECK ----
            try:
                member_count = Users.query.filter_by(team_id=team.id).count()
            except Exception as e:
                debug(f"ERROR counting members: {e}")
                member_count = None

            debug(f"Team '{team.name}' currently has {member_count} members")

            if limit is not None and member_count is not None and member_count >= limit:
                debug(f"DENY join: Team is full for bracket '{bracket}'")
                return jsonify({
                    "success": False,
                    "message": (
                        f"JOIN BLOCKED Team '{team.name}' is full for bracket '{bracket}'. "
                        f"Limit: {limit} members. Current: {member_count}."
                    )
                }), 400

            debug(f"ALLOWED: User '{user_display}' can join team '{team.name}'")
            return

        return
