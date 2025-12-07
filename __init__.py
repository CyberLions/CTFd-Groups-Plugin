from flask import request, jsonify
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.modes import TEAMS_MODE
from CTFd.models import Users, Teams
from CTFd import utils

def load(app):
    # group -> team-member-limit (None = unlimited)
    GROUP_LIMITS = {
        "PSU": 4,
        "Educational": None,
        "Open": None
    }

    def debug(msg):
        print(f"[TEAM_LIMIT_PLUGIN] {msg}", flush=True)

    @app.before_request
    def team_limit_check():
        endpoint = request.endpoint
        
        debug(f"Incoming request to endpoint: {endpoint}")

        if not utils.get_config("user_mode") == TEAMS_MODE:
            debug("System not in TEAM mode — plugin bypassing.")
            return  # Only enforce if team mode enabled

        # Only enforce during join
        if endpoint not in ("api_teams_join",):
            debug(f"Endpoint {endpoint} does not require validation — skipping.")
            return

        json = request.get_json(silent=True) or {}
        debug(f"Received JSON payload: {json}")

        current_user = utils.get_current_user()
        if not current_user:
            debug("No logged-in user — cannot evaluate team rules.")
            return

        group = current_user.extra.get("group")
        username = current_user.username

        if not group:
            debug(f"User '{username}' has NO GROUP SET! Blocking team join.")
            return jsonify({
                "success": False,
                "message": "You do not have a group assigned. Contact event staff."
            }), 400

        # Validate group exists
        if group not in GROUP_LIMITS:
            debug(f"User '{username}' has INVALID GROUP '{group}' — Not recognized.")
            return jsonify({
                "success": False,
                "message": f"Invalid group '{group}'. Contact admins."
            }), 400
        
        debug(f"User '{username}' is in group '{group}'")

        team_id = json.get("team_id")
        if not team_id:
            debug("No team_id provided — cannot evaluate join attempt.")
            return jsonify({
                "success": False,
                "message": "No team ID provided in join request."
            }), 400
        
        team = Teams.query.filter_by(id=team_id).first()
        if not team:
            debug(f"Team with ID {team_id} not found.")
            return jsonify({
                "success": False,
                "message": f"Team with ID {team_id} does not exist."
            }), 404

        member_count = Users.query.filter_by(team_id=team_id).count()
        limit = GROUP_LIMITS.get(group)

        debug(f"Team '{team.name}' currently has {member_count} members. Group '{group}' limit = {limit}")

        if limit is not None and member_count >= limit:
            debug(f"DENIED — Team '{team.name}' has hit limit {limit} for group '{group}'")

            return jsonify({
                "success": False,
                "message": f"🛑 JOIN BLOCKED — Team '{team.name}' is full.\n"
                           f"Group '{group}' is limited to {limit} members.\n"
                           f"Current members: {member_count}"
            }), 400

        debug(f"SUCCESS — User '{username}' CAN join team '{team.name}'")

        # Allowed to proceed
        return
