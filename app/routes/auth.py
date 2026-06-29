from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User, Project, ProjectMembership

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('project.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not getattr(user, 'active', True):
                flash('This account is disabled. Contact the administrator.', 'error')
                return render_template('auth/login.html')
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('project.dashboard'))

        flash('Invalid username or password.', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    # Public self-registration is disabled. Only an administrator may create
    # accounts, so access to the app is controlled entirely by the admin.
    if current_user.role != 'admin':
        flash('Only an administrator can create accounts.', 'error')
        return redirect(url_for('project.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'annotator')
        if role not in ('admin', 'annotator', 'reviewer'):
            role = 'annotator'

        if not username:
            flash('Username is required.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Username already taken.', 'error')
        elif email and User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            user = User(username=username, email=email or f'{username}@local', role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            # Do NOT log in as the new user — the admin stays logged in.
            flash(f'Account "{username}" created ({role}).', 'success')
            return redirect(url_for('auth.register'))

    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


# ── User management (admin only) ────────────────────────────────────────────────

def _require_admin():
    """Return None if current user is admin, else a redirect response."""
    if current_user.role != 'admin':
        flash('Administrator access required.', 'error')
        return redirect(url_for('project.dashboard'))
    return None


def _admin_count():
    return User.query.filter_by(role='admin', active=True).count()


@auth_bp.route('/users')
@login_required
def users():
    guard = _require_admin()
    if guard:
        return guard
    all_users = User.query.order_by(User.username).all()
    projects = Project.query.order_by(Project.name).all()
    proj_by_id = {p.id: p for p in projects}
    owned_ids = {}      # user_id -> set(project_id) they own
    for p in projects:
        owned_ids.setdefault(p.created_by, set()).add(p.id)
    memberships = {}    # user_id -> list of ProjectMembership
    for m in ProjectMembership.query.all():
        memberships.setdefault(m.user_id, []).append(m)
    return render_template('auth/users.html', users=all_users,
                           projects=projects, proj_by_id=proj_by_id,
                           owned_ids=owned_ids, memberships=memberships)


@auth_bp.route('/users/<int:user_id>/projects/add', methods=['POST'])
@login_required
def grant_membership(user_id):
    """Admin tool: give a user access to a project (repairs broken shares)."""
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    project = Project.query.get_or_404(int(request.form.get('project_id', 0) or 0))
    role = request.form.get('role', 'annotator')
    if role not in ('admin', 'annotator', 'reviewer'):
        role = 'annotator'
    existing = ProjectMembership.query.filter_by(user_id=user.id, project_id=project.id).first()
    if existing:
        flash(f'{user.username} already has access to "{project.name}".', 'error')
    else:
        db.session.add(ProjectMembership(user_id=user.id, project_id=project.id, role=role))
        db.session.commit()
        flash(f'Granted {user.username} access to "{project.name}" ({role}).', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/projects/<int:project_id>/remove', methods=['POST'])
@login_required
def revoke_membership(user_id, project_id):
    """Admin tool: remove a user's access to a project."""
    guard = _require_admin()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    if user_id == project.created_by:
        flash('The project owner cannot be removed.', 'error')
        return redirect(url_for('auth.users'))
    m = ProjectMembership.query.filter_by(user_id=user_id, project_id=project_id).first()
    if not m:
        flash('No such membership.', 'error')
    else:
        db.session.delete(m)
        db.session.commit()
        flash('Access removed.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/role', methods=['POST'])
@login_required
def set_role(user_id):
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', '')
    if role not in ('admin', 'annotator', 'reviewer'):
        flash('Invalid role.', 'error')
    elif user.id == current_user.id and role != 'admin':
        flash('You cannot remove your own admin role.', 'error')
    elif user.role == 'admin' and role != 'admin' and _admin_count() <= 1:
        flash('Cannot demote the last administrator.', 'error')
    else:
        user.role = role
        db.session.commit()
        flash(f'{user.username} is now {role}.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/password', methods=['POST'])
@login_required
def reset_password(user_id):
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    pw = request.form.get('password', '')
    if len(pw) < 6:
        flash('Password must be at least 6 characters.', 'error')
    else:
        user.set_password(pw)
        db.session.commit()
        flash(f'Password reset for {user.username}.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/active', methods=['POST'])
@login_required
def toggle_active(user_id):
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot disable your own account.', 'error')
    elif user.active and user.role == 'admin' and _admin_count() <= 1:
        flash('Cannot disable the last administrator.', 'error')
    else:
        user.active = not user.active
        db.session.commit()
        flash(f'{user.username} {"enabled" if user.active else "disabled"}.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
    elif user.role == 'admin' and _admin_count() <= 1:
        flash('Cannot delete the last administrator.', 'error')
    else:
        name = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f'Deleted user {name}.', 'success')
    return redirect(url_for('auth.users'))
