from flask import request, jsonify
from CTFd.utils.modes import TEAMS_MODE
from CTFd.models import Users, Teams
from CTFd import utils

def load(app):
    # per-bracket limits (None = unlimited)
    BRACKET_LIMITS = {
        "PSU": 4,
        "Academic": None,
        "Open": None,
        # add other bracket display-names here if you want to limit them later
    }

    def debug(msg):
        print(f"[TEAM_LIMIT_PLUGIN] {msg}", flush=True)

    def get_team_bracket_from_team_obj(team):
        """
        Defensive: try a few possible places where a bracket might be stored on the Team object.
        """
        if team is None:
            return None

        # 1) direct attribute (common)
        for attr in ("bracket", "bracket_id", "scoreboard_bracket", "scoreboard_bracket_id"):
            try:
                value = getattr(team, attr, None)
                if value:
                    # If it's an object with a name property (some versions), normalize to str(name)
                    name = getattr(value, "name", None)
                    if name:
                        return str(name)
                    return str(value)
            except Exception:
                continue

        # 2) maybe stored in extra JSON
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
        """
        When joining from the HTML form, the team may be provided by:
        - team_id
        - name
        - team
        We'll try them in order.
        """
        team = None
        # try numeric id fields
        for key in ("team_id", "teamid", "team"):
            if key in form and form.get(key):
                val = form.get(key)
                # numeric id?
                if val.isdigit():
                    team = Teams.query.filter_by(id=int(val)).first()
                    if team:
                        return team
                # otherwise maybe it's a name
                team = Teams.query.filter_by(name=val).first()
                if team:
                    return team

        # fallback: maybe form uses 'name' field
        if "name" in form and form.get("name"):
            team = Teams.query.filter_by(name=form.get("name")).first()
            if team:
                return team

        return None

    @app.before_request
    def team_limit_check():
        endpoint = request.endpoint
        debug(f"Incoming request to endpoint: {endpoint}")

        # Only operate when in team mode
        if not utils.get_config("user_mode") == TEAMS_MODE:
            debug("Not in TEAMS mode, skipping validation.")
            return

        # We only care about team creation POST and team join POST
        # - teams.new handles the create-team form (POST)
        # - teams.join handles the join-team form (POST)
        # - keep api_teams_join as extra in case it's used by your theme
        if endpoint not in ("teams.new", "teams.join", "api_teams_join"):
            debug(f"Endpoint {endpoint} does not require validation — skipping.")
            return

        # Work with the form/payload
        form = request.form or {}
        json_payload = request.get_json(silent=True) or {}
        debug(f"Form keys: {list(form.keys())}; JSON keys: {list(json_payload.keys())}")

        user = utils.get_current_user()
        user_display = getattr(user, "name", None) or getattr(user, "username", None) or "unknown"
        debug(f"Current user: {user_display}")

        # ---- TEAM CREATION: teams.new (POST) ----
        # Allow team creation to proceed (first member) but log the bracket selected.
        if endpoint == "teams.new" and request.method == "POST":
            # Try to extract bracket from form first
            bracket_val = None
            for key in ("bracket", "bracket_id", "scoreboard_bracket", "scoreboard_bracket_id"):
                if key in form and form.get(key):
                    bracket_val = form.get(key)
                    break

            # some forms might send bracket as a numeric id - if so try to resolve to name
            if bracket_val and bracket_val.isdigit():
                # attempt to find bracket by id through Teams table (rare), otherwise leave numeric
                try:
                    # if teams use a bracket relationship later, leave numeric as-is
                    pass
                except Exception:
                    pass

            debug(f"Team creation requested by '{user_display}'. Bracket choice (form) = {bracket_val}")

            # nothing to block at creation (first member becomes captain)
            return

        # ---- TEAM JOIN: teams.join (POST) or api_teams_join ----
        # We must find the team being joined and determine its bracket
        if endpoint in ("teams.join", "api_teams_join") and request.method in ("POST", "PUT"):
            # The form could send team id or name, the api could send json team_id
            team = None
            if json_payload.get("team_id"):
                try:
                    team = Teams.query.filter_by(id=int(json_payload.get("team_id"))).first()
                except Exception:
                    team = None

            # if not found from json, try form
            if not team:
                team = find_team_by_form(form)

            if not team:
                debug("Could not determine the target team from the request (no team_id/name).")
                # If we can't find team, don't block — let normal handler return error.
                return

            debug(f"Target team resolved: id={team.id} name='{getattr(team, 'name', '')}'")

            # determine bracket name for the team
            bracket = get_team_bracket_from_team_obj(team)
            debug(f"Bracket (from team object) resolved to: {bracket}")

            # As a fallback, the join form might include a bracket field (rare)
            if not bracket:
                for key in ("bracket", "bracket_id", "scoreboard_bracket"):
                    if key in form and form.get(key):
                        bracket = form.get(key)
                        debug(f"Bracket (from join form) = {bracket}")
                        break

            # If bracket still None, we can't apply per-bracket rules (so allow)
            if not bracket:
                debug("No bracket found for team; skipping per-bracket limit checks.")
                return

            # Normalize bracket string
            bracket = str(bracket).strip()

            # Look up configured limit
            limit = BRACKET_LIMITS.get(bracket)
            debug(f"Bracket '{bracket}' limit = {limit}")

            # Count current members
            try:
                member_count = Users.query.filter_by(team_id=team.id).count()
            except Exception as e:
                debug(f"ERROR counting members for team {team.id}: {e}")
                member_count = None

            debug(f"Team '{getattr(team,'name','<unknown>')}' currently has {member_count} members")

            # Enforce limit (if defined)
            if limit is not None and member_count is not None and member_count >= limit:
                debug(f"DENY join: Team has {member_count} members which meets/exceeds limit {limit} for bracket '{bracket}'")
                # return a JSON error if this is an API call, otherwise return a JSON response
                return jsonify({
                    "success": False,
                    "message": (
                        f"🛑 JOIN BLOCKED — Team '{getattr(team,'name','<unknown>')}' is full for bracket '{bracket}'. "
                        f"Limit: {limit} members. Current: {member_count}."
                    )
                }), 400

            debug(f"ALLOWED: User '{user_display}' can join team '{getattr(team,'name','<unknown>')}' (bracket '{bracket}')")
            return

        # default: nothing to do
        return
