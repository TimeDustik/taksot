import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'super_fifo_key_v6'

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20))
    card_number = db.Column(db.String(20))
    leader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    must_change_password = db.Column(db.Boolean, default=True)
    expenses = db.relationship('Expense', backref='owner', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    remaining = db.Column(db.Float, nullable=False)
    region = db.Column(db.String(100))
    date = db.Column(db.String(20))
    receipt_img = db.Column(db.String(200))
    status = db.Column(db.String(20), default='Очікує')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))


with app.app_context():
    db.create_all()


# ЛОГІКА FIFO ВИПЛАТИ
@app.route('/pay_user/<int:user_id>', methods=['POST'])
def pay_user(user_id):
    if session.get('role') != 'teamlead': return redirect(url_for('login'))

    amount_to_pay = float(request.form.get('pay_amount', 0))
    if amount_to_pay <= 0: return redirect(url_for('dashboard'))

    # Знаходимо всі схвалені чеки юзера, сортуємо за ID (найстаріші перші)
    pending_expenses = Expense.query.filter_by(user_id=user_id, status='Схвалено').order_by(Expense.id.asc()).all()

    for exp in pending_expenses:
        if amount_to_pay <= 0:
            break

        if amount_to_pay >= exp.remaining:
            # Виплата покриває весь чек або більше
            amount_to_pay -= exp.remaining
            exp.remaining = 0
            exp.status = 'Виплачено'
        else:
            # Виплата покриває лише частину чека
            exp.remaining -= amount_to_pay
            amount_to_pay = 0

    db.session.commit()
    flash('Виплату проведено успішно')
    return redirect(url_for('dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']):
            session['user_id'] = user.id
            session['role'] = user.role
            return redirect(url_for('change_password')) if user.must_change_password else redirect(url_for('dashboard'))
        flash('Помилка входу')
    return render_template('login.html')


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])

    if user.role == 'admin':
        all_users = User.query.filter(User.role != 'admin').order_by(User.role.desc()).all()
        return render_template('admin.html', users=all_users, teamleads=User.query.filter_by(role='teamlead').all())

    if user.role == 'teamlead':
        subs = User.query.filter_by(leader_id=user.id).all()
        # Нові заявки на розгляд (свої + підлеглі)
        pending = Expense.query.join(User).filter(((User.leader_id == user.id) | (User.id == user.id)),
                                                  Expense.status == 'Очікує').all()
        return render_template('teamlead.html', subs=subs, pending=pending, user=user)

    if user.role == 'l1':
        my_exps = Expense.query.filter_by(user_id=user.id).order_by(Expense.id.desc()).all()
        return render_template('l1.html', expenses=my_exps, user=user)


@app.route('/process_expense/<int:id>/<action>', methods=['POST'])
def process_expense(id, action):
    exp = Expense.query.get_or_404(id)
    if action == 'approve':
        exp.status = 'Схвалено'
    elif action == 'reject':
        exp.status = 'Відхилено'
    db.session.commit()
    return redirect(request.referrer)


@app.route('/add_expense', methods=['POST'])
def add_expense():
    file = request.files.get('receipt')
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        amt = float(request.form['amount'])
        new_exp = Expense(amount=amt, remaining=amt, region=request.form['region'], date=request.form['date'],
                          receipt_img=filename, user_id=session['user_id'])
        db.session.add(new_exp)
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/user_history/<int:user_id>')
def user_history(user_id):
    u = User.query.get_or_404(user_id)
    exps = Expense.query.filter_by(user_id=user_id).order_by(Expense.id.desc()).all()
    return render_template('user_history.html', target_user=u, expenses=exps)


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


@app.route('/delete_expense/<int:id>', methods=['POST'])
def delete_expense(id):
    exp = Expense.query.get_or_404(id)
    db.session.delete(exp)
    db.session.commit()
    return redirect(request.referrer)


@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if request.method == 'POST':
        user = User.query.get(session['user_id'])
        user.set_password(request.form['new_password'])
        user.must_change_password = False
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('change_password.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)