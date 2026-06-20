from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User

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
