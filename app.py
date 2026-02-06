import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from io import BytesIO

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'ultra_final_v12_stable'

db = SQLAlchemy(app)


# --- МОДЕЛІ ДАНИХ ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20))  # 'admin', 'teamlead', 'l1'
    card_number = db.Column(db.String(20))
    leader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    must_change_password = db.Column(db.Boolean, default=True)

    expenses = db.relationship('Expense', backref='owner', lazy=True)
    leader = db.relationship('User', remote_side=[id], backref='subordinates')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    remaining = db.Column(db.Float, nullable=False)
    region = db.Column(db.String(100))
    date = db.Column(db.String(20))  # YYYY-MM-DD
    month_year = db.Column(db.String(7))  # YYYY-MM
    comment = db.Column(db.Text)
    manager_contact = db.Column(db.String(100))
    receipt_img = db.Column(db.String(200))
    status = db.Column(db.String(20), default='Очікує')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))


# Ініціалізація бази
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin', must_change_password=False)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()


# --- АВТОРИЗАЦІЯ ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']):
            session['user_id'] = user.id
            session['role'] = user.role
            return redirect(url_for('change_password')) if user.must_change_password else redirect(url_for('dashboard'))
        flash('Невірний логін або пароль')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        user = User.query.get(session['user_id'])
        user.set_password(request.form['new_password'])
        user.must_change_password = False
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('change_password.html')


# --- DASHBOARD ТА ФІЛЬТРАЦІЯ ---

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])

    now_month = datetime.now().strftime('%Y-%m')
    selected_month = request.args.get('month', now_month)

    all_months_raw = db.session.query(Expense.month_year).distinct().all()
    available_months = sorted([m[0] for m in all_months_raw if m[0]], reverse=True)
    if now_month not in available_months:
        available_months.insert(0, now_month)

    if user.role == 'admin':
        all_users = User.query.filter(User.role != 'admin').order_by(User.role.desc()).all()
        teamleads = User.query.filter_by(role='teamlead').all()
        return render_template('admin.html', users=all_users, teamleads=teamleads)

    if user.role == 'teamlead':
        subs = User.query.filter((User.leader_id == user.id) | (User.id == user.id)).all()
        pending = Expense.query.join(User).filter(((User.leader_id == user.id) | (User.id == user.id)),
                                                  Expense.status == 'Очікує').all()
        my_history = Expense.query.filter_by(user_id=user.id, month_year=selected_month).order_by(
            Expense.id.desc()).all()
        return render_template('teamlead.html', subs=subs, pending=pending, my_expenses=my_history, user=user,
                               months=available_months, selected_month=selected_month)

    if user.role == 'l1':
        my_exps = Expense.query.filter_by(user_id=user.id, month_year=selected_month).order_by(Expense.id.desc()).all()
        return render_template('l1.html', expenses=my_exps, user=user, months=available_months,
                               selected_month=selected_month)


# --- ЛОГІКА ВИТРАТ ТА ВИПЛАТ ---

@app.route('/add_expense', methods=['POST'])
def add_expense():
    if 'user_id' not in session: return redirect(url_for('login'))
    file = request.files.get('receipt')
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        date_str = request.form['date']
        amt = float(request.form['amount'])

        # ВИПРАВЛЕННЯ: Очищення регіону при додаванні
        raw_region = request.form['region']
        clean_region = raw_region.strip().capitalize() if raw_region else "Не вказано"

        new_exp = Expense(
            amount=amt, remaining=amt, region=clean_region,
            date=date_str, month_year=date_str[:7],
            comment=request.form.get('comment'), manager_contact=request.form.get('manager_contact'),
            receipt_img=filename, user_id=session['user_id']
        )
        db.session.add(new_exp)
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/pay_user/<int:user_id>', methods=['POST'])
def pay_user(user_id):
    if session.get('role') != 'teamlead': return "Access Denied", 403
    amount_to_pay = float(request.form.get('pay_amount', 0))

    # FIFO: Спочатку закриваємо найстаріші схвалені чеки
    pending = Expense.query.filter_by(user_id=user_id, status='Схвалено').order_by(Expense.id.asc()).all()
    for exp in pending:
        if amount_to_pay <= 0: break
        if amount_to_pay >= exp.remaining:
            amount_to_pay -= exp.remaining
            exp.remaining = 0
            exp.status = 'Виплачено'
        else:
            exp.remaining -= amount_to_pay
            amount_to_pay = 0
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/process_expense/<int:id>/<action>', methods=['POST'])
def process_expense(id, action):
    exp = Expense.query.get_or_404(id)
    if action == 'approve':
        exp.status = 'Схвалено'
    elif action == 'reject':
        exp.status = 'Відхилено'
    db.session.commit()
    return redirect(request.referrer)


@app.route('/delete_expense/<int:id>', methods=['POST'])
def delete_expense(id):
    exp = Expense.query.get_or_404(id)
    db.session.delete(exp)
    db.session.commit()
    return redirect(request.referrer)


# --- ЕКСПОРТ EXCEL (ВИПРАВЛЕННЯ ДУБЛІКАТІВ РЕГІОНІВ) ---

@app.route('/export_excel')
def export_excel():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    sel_month = request.args.get('month', datetime.now().strftime('%Y-%m'))

    query = Expense.query.filter(Expense.month_year == sel_month)
    if user.role == 'teamlead':
        query = query.join(User).filter((User.leader_id == user.id) | (User.id == user.id))
    elif user.role == 'l1':
        query = query.filter(Expense.user_id == user.id)

    expenses = query.all()

    # Аркуш 1: Деталі
    df_det = pd.DataFrame([{
        "Співробітник": e.owner.username, "Сума": e.amount, "Регіон": e.region,
        "Дата": e.date, "Коментар": e.comment, "Керівник": e.manager_contact
    } for e in expenses])

    # Аркуш 2: Регіони (ВИПРАВЛЕНО: Стандартизація назв)
    reg_list = []
    for e in expenses:
        clean_reg = e.region.strip().capitalize() if e.region else "Не вказано"
        reg_list.append({"Регіон": clean_reg, "Сума до виплати": e.remaining})

    if reg_list:
        df_reg = pd.DataFrame(reg_list).groupby("Регіон").sum().reset_index()
    else:
        df_reg = pd.DataFrame(columns=["Регіон", "Сума до виплати"])

    # Аркуш 3: Співробітники
    emp_list = [{"Співробітник": e.owner.username, "Трати": e.amount, "Борг": e.remaining} for e in expenses]
    if emp_list:
        df_emp = pd.DataFrame(emp_list).groupby("Співробітник").sum().reset_index()
        total_row = pd.DataFrame(
            [{"Співробітник": "ВСОГО:", "Трати": df_emp["Трати"].sum(), "Борг": df_emp["Борг"].sum()}])
        df_emp = pd.concat([df_emp, total_row], ignore_index=True)
    else:
        df_emp = pd.DataFrame(columns=["Співробітник", "Трати", "Борг"])

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_det.to_excel(writer, index=False, sheet_name='1. Детальний звіт')
        df_reg.to_excel(writer, index=False, sheet_name='2. По регіонах')
        df_emp.to_excel(writer, index=False, sheet_name='3. Підсумок')

    output.seek(0)
    return send_file(output, download_name=f"Report_{sel_month}.xlsx", as_attachment=True)


# --- АДМІН ПАНЕЛЬ ---

@app.route('/create_user', methods=['POST'])
def create_user():
    new_user = User(username=request.form['username'], role=request.form['role'],
                    card_number=request.form.get('card_number'), leader_id=request.form.get('leader_id') or None)
    new_user.set_password("Dfg@321Dfg")
    db.session.add(new_user)
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/edit_user/<int:id>', methods=['POST'])
def edit_user(id):
    u = User.query.get_or_404(id)
    u.username = request.form['username']
    u.role = request.form['role']
    u.card_number = request.form['card_number']
    u.leader_id = request.form.get('leader_id') or None
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/reset_password/<int:id>', methods=['POST'])
def reset_password(id):
    u = User.query.get_or_404(id)
    u.set_password("Dfg@321Dfg")
    u.must_change_password = True
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/user_history/<int:user_id>')
def user_history(user_id):
    u = User.query.get_or_404(user_id)
    exps = Expense.query.filter_by(user_id=user_id).order_by(Expense.id.desc()).all()
    return render_template('user_history.html', target_user=u, expenses=exps)


if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)