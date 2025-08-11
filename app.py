from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from config import Config
from models import db, User, Product, Order, OrderItem, Ticket, Feedback
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import stripe

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = 'login'
    login_manager.init_app(app)

    stripe.api_key = app.config.get('STRIPE_SECRET_KEY')

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ---------- Auth ----------
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                flash('Logged in successfully.', 'success')
                return redirect(url_for('admin_menu' if user.is_admin else 'menu'))
            flash('Invalid email or password.', 'danger')
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            if User.query.filter_by(email=email).first():
                flash('Email already registered.', 'danger')
                return redirect(url_for('register'))
            hashed = generate_password_hash(password)
            user = User(name=name, email=email, password_hash=hashed, wallet=0)
            db.session.add(user)
            db.session.commit()
            flash('Registration successful. Please login.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Logged out.', 'info')
        return redirect(url_for('login'))

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('admin_menu' if current_user.is_admin else 'menu'))
        return redirect(url_for('login'))

    # ---------- Menu ----------
    @app.route('/menu')
    @login_required
    def menu():
        products = Product.query.filter_by(available=True).order_by(Product.id).all()
        return render_template('menu.html', products=products)

    # ---------- Cart ----------
    @app.route('/add_to_cart/<int:product_id>')
    @login_required
    def add_to_cart(product_id):
        product = Product.query.get_or_404(product_id)
        if not product.available:
            flash('This product is not available.', 'danger')
            return redirect(url_for('menu'))
        cart = session.get('cart', {})
        cart[str(product_id)] = cart.get(str(product_id), 0) + 1
        session['cart'] = cart
        flash(f"Added {product.name} to cart", 'success')
        return redirect(url_for('menu'))

    @app.route('/cart')
    @login_required
    def cart():
        cart = session.get('cart', {})
        items = []
        total = 0
        for pid, qty in cart.items():
            product = Product.query.get(int(pid))
            if product:
                subtotal = product.price * qty
                items.append({'product': product, 'qty': qty, 'subtotal': subtotal})
                total += subtotal
        return render_template('cart.html', items=items, total=total,
                               stripe_publishable_key=app.config.get('STRIPE_PUBLISHABLE_KEY'))

    @app.route('/remove_from_cart/<int:product_id>')
    @login_required
    def remove_from_cart(product_id):
        cart = session.get('cart', {})
        cart.pop(str(product_id), None)
        session['cart'] = cart
        flash('Item removed from cart', 'info')
        return redirect(url_for('cart'))

    # ---------- Wallet Checkout ----------
    @app.route('/checkout', methods=['POST'])
    @login_required
    def checkout():
        cart = session.get('cart', {})
        if not cart:
            flash('Your cart is empty', 'danger')
            return redirect(url_for('menu'))

        total = 0
        for pid, qty in cart.items():
            product = Product.query.get(int(pid))
            if product:
                total += product.price * qty

        if current_user.wallet < total:
            flash('Insufficient wallet balance. Please recharge.', 'danger')
            return redirect(url_for('cart'))

        # Deduct wallet balance
        current_user.wallet -= total
        order = Order(user_id=current_user.id, total_amount=total)
        db.session.add(order)
        db.session.flush()  # get order.id

        # Add items
        for pid, qty in cart.items():
            product = Product.query.get(int(pid))
            if product:
                item = OrderItem(order_id=order.id, product_id=product.id, quantity=qty, price=product.price)
                db.session.add(item)

        db.session.commit()
        session['cart'] = {}
        flash('Order placed successfully', 'success')
        return redirect(url_for('orders'))

    # ---------- Stripe Checkout ----------
    @app.route('/stripe_checkout', methods=['POST'])
    @login_required
    def stripe_checkout():
        cart = session.get('cart', {})
        if not cart:
            flash('Your cart is empty', 'danger')
            return redirect(url_for('menu'))

        total_amount = 0
        line_items = []
        for pid, qty in cart.items():
            product = Product.query.get(int(pid))
            if product:
                total_amount += product.price * qty
                line_items.append({
                    'price_data': {
                        'currency': 'inr',
                        'product_data': {'name': product.name},
                        'unit_amount': int(product.price * 100)
                    },
                    'quantity': qty
                })

        session_data = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url=url_for('payment_success', _external=True),
            cancel_url=url_for('cart', _external=True)
        )
        return redirect(session_data.url, code=303)

    @app.route('/payment_success')
    @login_required
    def payment_success():
        # After payment success, save order to DB
        cart = session.get('cart', {})
        if not cart:
            return redirect(url_for('menu'))

        total = 0
        order = Order(user_id=current_user.id, total_amount=0)
        db.session.add(order)
        db.session.flush()

        for pid, qty in cart.items():
            product = Product.query.get(int(pid))
            if product:
                total += product.price * qty
                item = OrderItem(order_id=order.id, product_id=product.id, quantity=qty, price=product.price)
                db.session.add(item)

        order.total_amount = total
        db.session.commit()
        session['cart'] = {}
        flash("Payment successful! Order placed.", "success")
        return redirect(url_for('orders'))

    # ---------- Orders ----------
    @app.route('/orders')
    @login_required
    def orders():
        user_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
        return render_template('order_history.html', orders=user_orders)

    # ---------- Tickets ----------
    @app.route('/tickets', methods=['GET', 'POST'])
    @login_required
    def tickets():
        if request.method == 'POST':
            subject = request.form.get('subject')
            category = request.form.get('category')
            message = request.form.get('message')
            t = Ticket(user_id=current_user.id, subject=subject, category=category, message=message)
            db.session.add(t)
            db.session.commit()
            flash('Ticket submitted.', 'success')
            return redirect(url_for('tickets'))
        user_tickets = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.created_at.desc()).all()
        return render_template('tickets.html', tickets=user_tickets)

    @app.route('/feedback', methods=['GET', 'POST'])
    @login_required
    def feedback():
        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            message = request.form.get('message')
            fb = Feedback(user_name=name, email=email, message=message)
            db.session.add(fb)
            db.session.commit()
            flash('Feedback sent.', 'success')
            return redirect(url_for('feedback'))
        return render_template('feedback.html')

    # ---------- Admin ----------
    def admin_required():
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)

    @app.route('/admin/menu', methods=['GET', 'POST'])
    @login_required
    def admin_menu():
        admin_required()
        if request.method == 'POST':
            name = request.form.get('name')
            price = float(request.form.get('price') or 0)
            desc = request.form.get('description')
            p = Product(name=name, price=price, description=desc, available=True)
            db.session.add(p)
            db.session.commit()
            flash('Product added.', 'success')
            return redirect(url_for('admin_menu'))
        products = Product.query.all()
        return render_template('admin_menu.html', products=products)

    @app.route('/admin/recharge', methods=['GET', 'POST'])
    @login_required
    def admin_recharge():
        admin_required()
        users = User.query.all()
        if request.method == 'POST':
            user_id = request.form.get('user_id')
            amount = float(request.form.get('amount') or 0)
            user = User.query.get(user_id)
            if user:
                user.wallet = (user.wallet or 0) + amount
                db.session.commit()
                flash(f"Recharged Rs.{amount:.2f} to {user.name}", "success")
            else:
                flash("User not found", "danger")
            return redirect(url_for('admin_recharge'))
        return render_template('admin_recharge.html', users=users)

    @app.route('/admin/orders')
    @login_required
    def admin_orders():
        admin_required()
        orders = Order.query.order_by(Order.created_at.desc()).all()
        return render_template('admin_orders.html', orders=orders)

    @app.route('/admin/toggle_product/<int:product_id>')
    @login_required
    def admin_toggle_product(product_id):
        admin_required()
        product = Product.query.get_or_404(product_id)
        product.available = not product.available
        db.session.commit()
        flash(f"{'Activated' if product.available else 'Deactivated'} {product.name}", 'info')
        return redirect(url_for('admin_menu'))

    @app.route('/admin/delete_product/<int:product_id>')
    @login_required
    def admin_delete_product(product_id):
        admin_required()
        product = Product.query.get_or_404(product_id)
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully', 'success')
        return redirect(url_for('admin_menu'))

    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)
